import asyncio
from typing import Any, Optional, List, AsyncGenerator, Type, TypeVar
from dataclasses import dataclass
import os
import json
import structlog
from pydantic import BaseModel
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryError
)


# ============================================================================
# Exceptions
# ============================================================================

class LLMError(Exception):
    """Base exception for LLM errors."""
    pass


class LLMRateLimitError(LLMError):
    """Rate limit exceeded."""
    pass


class LLMTimeoutError(LLMError):
    """Request timeout."""
    pass


class LLMParseError(LLMError):
    """Failed to parse structured output."""
    pass

# ============================================================================
# Configuration
# ============================================================================

DEFAULT_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful AI assistant specialized in financial analysis and research."
)
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TIMEOUT = 120


@dataclass
class LLMConfig:
    """Global LLM configuration."""
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    default_model: str = DEFAULT_MODEL
    default_max_tokens: int = DEFAULT_MAX_TOKENS
    default_temperature: float = DEFAULT_TEMPERATURE
    timeout: int = DEFAULT_TIMEOUT
    retry_attempts: int = 3
    retry_min_wait: float = 1.0
    retry_max_wait: float = 10.0


# Global config instance
_llm_config = LLMConfig()


def configure_llm(config: LLMConfig) -> None:
    """Configure global LLM settings."""
    global _llm_config, _client_instance
    _llm_config = config
    _client_instance = None
    _client_instances.clear()


def get_llm_config() -> LLMConfig:
    """Get current LLM configuration."""
    return _llm_config


# ============================================================================
# Client Initialization
# ============================================================================

_client_instance = None  # Legacy name retained for existing callers.
_client_instances = {}
_client_lock = asyncio.Lock()

# LangChain provider imports - make optional so module can be imported even if
# some provider packages are not installed. Each provider var is initialized
# to None and set only when the import succeeds.
ChatAnthropic = None
ChatOpenAI = None
ChatOllama = None
try:
    from langchain_anthropic import ChatAnthropic
except Exception:
    ChatAnthropic = None

try:
    from langchain_openai import ChatOpenAI
except Exception:
    ChatOpenAI = None

try:
    from langchain_ollama import ChatOllama
except Exception:
    ChatOllama = None

# Optional LangChain async callback handler (used for streaming tokens)
try:
    from langchain.callbacks.base import AsyncCallbackHandler
except Exception:
    AsyncCallbackHandler = None


async def get_llm_client(model_name: Optional[str] = None):
    """Cache clients by selected model to prevent cross-provider/model reuse."""
    global _client_instance
    model = model_name or get_llm_config().default_model
    if not isinstance(model, str) or not model.strip():
        raise ValueError("LLM model must be non-empty")
    if model in _client_instances:
        return _client_instances[model]
    async with _client_lock:
        if model not in _client_instances:
            from ..model.llm import get_chat_model
            client = get_chat_model(model)
            _client_instances[model] = client
            structlog.get_logger(__name__).info("llm_client_initialized", model=model)
        _client_instance = _client_instances[model]
        return _client_instances[model]


# ============================================================================
# Exceptions
# ============================================================================

class LLMError(Exception):
    """Base exception for LLM operations."""
    pass


class LLMRateLimitError(LLMError):
    """Rate limit exceeded."""
    pass


class LLMTimeoutError(LLMError):
    """Request timeout."""
    pass


class LLMParseError(LLMError):
    """Failed to parse structured output."""
    pass


# ============================================================================
# Helper Functions
# ============================================================================

def _build_system_prompt_with_tools(
    base_system_prompt: str,
    tools: Optional[List[Any]]
) -> str:
    """
    Build system prompt with tool descriptions.
    
    Args:
        base_system_prompt: Base system prompt
        tools: Optional list of tools
        
    Returns:
        Enhanced system prompt with tool descriptions
    """
    if not tools:
        return base_system_prompt
    
    tool_descriptions = []
    for tool in tools:
        try:
            # Try to get description attribute
            if hasattr(tool, 'description'):
                desc = tool.description
            elif hasattr(tool, '__doc__') and tool.__doc__:
                desc = tool.__doc__.strip()
            elif hasattr(tool, 'name'):
                desc = f"Tool: {tool.name}"
            else:
                desc = "Tool (no description available)"
            
            tool_descriptions.append(f"- {desc}")
            
        except Exception:
            # Skip tools we can't describe
            continue
    
    if not tool_descriptions:
        return base_system_prompt
    
    return (
        f"{base_system_prompt}\n\n"
        "## Available Tools\n" +
        "\n".join(tool_descriptions)
    )


def _classify_error(error: Exception) -> Exception:
    """
    Classify error for retry logic.
    
    Args:
        error: Original exception
        
    Returns:
        Classified exception (may be same or wrapped)
    """
    error_str = str(error).lower()
    error_type = type(error).__name__
    
    # Rate limit errors
    if any(indicator in error_str for indicator in ['rate_limit', 'rate limit', '429']):
        return LLMRateLimitError(str(error))
    
    # Timeout errors
    if 'timeout' in error_str or isinstance(error, asyncio.TimeoutError):
        return LLMTimeoutError(str(error))
    
    # Connection errors (retryable)
    if any(indicator in error_str for indicator in ['connection', 'network', 'unavailable']):
        return LLMError(f"Connection error: {error}")
    
    # Everything else is non-retryable
    return error


T = TypeVar('T', bound=BaseModel)


# ============================================================================
# Demo / Production LLM client adapters (from xllm.py demo)
# Provide a ProductionLLMClient and MockLLMClient that wrap the underlying
# LangChain client returned by get_llm_client(). These are convenience
# adapters useful for demos, tests, and code that expects a client object
# with `complete`/`stream` methods.
# ============================================================================


class ProductionLLMClient:
    """A production-grade client wrapper that exposes `complete` and
    `stream` async methods and implements retry, timeout handling and
    structured-output helpers. Internally uses `get_llm_client()`.
    """

    def __init__(self, config: Optional[LLMConfig] = None, logger: Optional[Any] = None):
        self.config = config or get_llm_config()
        self.logger = logger or structlog.get_logger(__name__)

    async def complete(self,
                       prompt: str,
                       system_prompt: Optional[str] = None,
                       model: Optional[str] = None,
                       max_tokens: Optional[int] = None,
                       temperature: Optional[float] = None,
                       **kwargs) -> str:
        """Return a full completion as text."""
        cfg = self.config
        model = model or cfg.default_model
        system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        max_tokens = max_tokens or cfg.default_max_tokens
        temperature = temperature or cfg.default_temperature

        enhanced_system = _build_system_prompt_with_tools(system_prompt, kwargs.get('tools'))

        client = await get_llm_client()

        # Build messages wrapper for langchain-style apis
        try:
            # Prefer ChatModel-style generation when available
            if hasattr(client, 'messages') and hasattr(client.messages, 'create'):
                resp = await asyncio.wait_for(
                    client.messages.create(
                        model=model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=enhanced_system,
                        messages=[{"role": "user", "content": prompt}],
                        **kwargs
                    ),
                    timeout=cfg.timeout
                )
            elif hasattr(client, 'agenerate'):
                # LangChain agenerate accepts a list of message lists
                messages_wrapper = [[{"role": "system", "content": enhanced_system}, {"role": "user", "content": prompt}]]
                try:
                    resp = await asyncio.wait_for(client.agenerate(messages_wrapper, **kwargs), timeout=cfg.timeout)
                except TypeError:
                    resp = await asyncio.wait_for(client.agenerate(messages=messages_wrapper, **kwargs), timeout=cfg.timeout)
            elif hasattr(client, 'apredict'):
                resp = await asyncio.wait_for(client.apredict(prompt, **kwargs), timeout=cfg.timeout)
            elif hasattr(client, 'predict'):
                resp = await asyncio.wait_for(asyncio.to_thread(lambda: client.predict(prompt, **kwargs)), timeout=cfg.timeout)
            else:
                raise RuntimeError("No supported API on underlying LLM client")

            # Normalize response to string
            if isinstance(resp, str):
                return resp
            if hasattr(resp, 'generations'):
                try:
                    gen = resp.generations[0][0]
                    if hasattr(gen, 'text'):
                        return gen.text
                    if hasattr(gen, 'message') and hasattr(gen.message, 'content'):
                        return gen.message.content
                except Exception:
                    return str(resp)
            if hasattr(resp, 'content'):
                if isinstance(resp.content, list):
                    return "".join(getattr(b, 'text', str(b)) for b in resp.content)
                return str(resp.content)

            return str(resp)

        except asyncio.TimeoutError:
            raise LLMTimeoutError(f"Request timed out after {cfg.timeout}s")
        except Exception as e:
            raise _classify_error(e)

    async def stream(self,
                     prompt: str,
                     system_prompt: Optional[str] = None,
                     model: Optional[str] = None,
                     max_tokens: Optional[int] = None,
                     temperature: Optional[float] = None,
                     **kwargs) -> AsyncGenerator[str, None]:
        """Attempt to stream tokens from the underlying client. Falls back to
        returning the full completion as a single chunk if true streaming is
        not available.
        """
        cfg = self.config
        model = model or cfg.default_model
        system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        max_tokens = max_tokens or cfg.default_max_tokens
        temperature = temperature or cfg.default_temperature

        enhanced_system = _build_system_prompt_with_tools(system_prompt, kwargs.get('tools'))

        client = await get_llm_client()

        # Try LangChain callback streaming
        if 'AsyncCallbackHandler' in globals() and AsyncCallbackHandler is not None and hasattr(client, 'agenerate'):
            q: asyncio.Queue = asyncio.Queue()

            class _QHandler(AsyncCallbackHandler):
                async def on_llm_new_token(self, token: str, **_):
                    await q.put(token)

                async def on_llm_end(self, **_):
                    await q.put(None)

            handler = _QHandler()

            async def _runner():
                try:
                    try:
                        await client.agenerate([[{"role": "system", "content": enhanced_system}, {"role": "user", "content": prompt}]], callbacks=[handler])
                    except TypeError:
                        await client.agenerate(messages=[[{"role": "system", "content": enhanced_system}, {"role": "user", "content": prompt}]], callbacks=[handler])
                except Exception:
                    try:
                        await q.put(None)
                    except Exception:
                        pass

            task = asyncio.create_task(_runner())

            try:
                while True:
                    token = await q.get()
                    if token is None:
                        break
                    if token:
                        yield token
                await task
                return
            except asyncio.CancelledError:
                task.cancel()
                raise

        # Provider SDK streaming
        if hasattr(client, 'messages') and hasattr(client.messages, 'stream'):
            async with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=enhanced_system,
                messages=[{"role": "user", "content": prompt}],
                **kwargs
            ) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield text
            return

        # Fallback: non-streaming
        content = await self.complete(prompt, system_prompt=system_prompt, model=model, max_tokens=max_tokens, temperature=temperature, **kwargs)
        if content:
            yield content


class MockLLMClient:
    """A simple mock client for tests and demos."""
    def __init__(self, responses: Optional[dict] = None):
        self.responses = responses or {}

    async def complete(self, prompt: str, **kwargs) -> str:
        await asyncio.sleep(0.01)
        for k, v in self.responses.items():
            if k.lower() in prompt.lower():
                return v
        return f"Mock response to: {prompt[:80]}"

    async def stream(self, prompt: str, **kwargs) -> AsyncGenerator[str, None]:
        text = await self.complete(prompt, **kwargs)
        for tok in text.split():
            await asyncio.sleep(0.005)
            yield tok + " "


def get_production_llm_client(use_mock: bool = False, mock_responses: Optional[dict] = None):
    """Factory: return a ProductionLLMClient or MockLLMClient instance."""
    if use_mock:
        return MockLLMClient(responses=mock_responses)
    return ProductionLLMClient()
