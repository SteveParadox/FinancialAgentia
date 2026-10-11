"""A minimal Python port of the TypeScript LLM wrapper (model/llm.ts).

This implements a best-effort mapping using python-langchain's ChatOpenAI model.
It intentionally supports OpenAI by default; other providers may be added later.
"""
import os
import asyncio
import json
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, retry_if_not_exception_type, RetryError
from openai import RateLimitError
import pybreaker
from typing import Any, Optional, List, AsyncGenerator, Type, TypeVar
from dataclasses import dataclass
import os
import json
import structlog
from pydantic import BaseModel
T = TypeVar("T", bound=BaseModel)


DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TIMEOUT = 120

# Try to import langchain providers; set None when not available so checks are safe
ChatOpenAI = None
ChatAnthropic = None
ChatGoogleGenerativeAI = None
ChatOllama = None
HumanMessage = None
SystemMessage = None
try:
    from langchain_openai import ChatOpenAI
except Exception:
    ChatOpenAI = None

try:
    from langchain_anthropic import ChatAnthropic  # type: ignore
except Exception:
    ChatAnthropic = None  # type: ignore

try:
    from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore
except Exception:
    ChatGoogleGenerativeAI = None  # type: ignore

try:
    from langchain_ollama import ChatOllama  # type: ignore
except Exception:
    ChatOllama = None  # type: ignore

try:
    from langchain_core.messages import HumanMessage, SystemMessage
except Exception:
    HumanMessage = None
    SystemMessage = None
try:
    from langchain.callbacks.base import AsyncCallbackHandler
except Exception:
    AsyncCallbackHandler = None

from ..utils._utils import (
    _classify_error,
    _build_system_prompt_with_tools,
    get_llm_client,
    get_llm_config,
    configure_llm,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMParseError,
)
DEFAULT_PROVIDER = "openai"
DEFAULT_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")  # Explicit provider selection
# DEFAULT_MODEL = "ollama-mistral:70b"  
DEFAULT_SYSTEM_PROMPT = (
    "You are FinancialAgentia, an autonomous financial research agent.\n"
    "Your primary objective is to conduct deep and thorough research on stocks and companies to answer user queries.\n"
    "You are equipped with a set of powerful tools to gather and analyze financial data.\n"
    "You should be methodical, breaking down complex questions into manageable steps and using your tools strategically to find the answers.\n"
    "Always aim to provide accurate, comprehensive, and well-structured information to the user."
)


import os

def get_chat_model(model_name: str = DEFAULT_MODEL, streaming: bool = False):
    """Select a provider by the explicitly configured model, fail closed."""
    if not isinstance(model_name, str) or not model_name:
        raise ValueError("model_name must be non-empty")
    if model_name.startswith("ollama-"):
        if ChatOllama is None:
            raise RuntimeError("langchain-ollama is required for Ollama models")
        is_cloud = model_name.endswith("-cloud")
        base_url = os.getenv(
            "OLLAMA_BASE_URL", "https://ollama.com" if is_cloud else "http://localhost:11434"
        )
        if is_cloud and not os.getenv("OLLAMA_API_KEY"):
            raise RuntimeError("OLLAMA_API_KEY not set for Ollama cloud model")
        model_clean = model_name.removeprefix("ollama-")
        return ChatOllama(model=model_clean, base_url=base_url, streaming=streaming)
    if model_name.startswith("claude-"):
        if ChatAnthropic is None:
            raise RuntimeError("langchain-anthropic required for Claude models")
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        return ChatAnthropic(model=model_name, anthropic_api_key=api_key, streaming=streaming)
    if model_name.startswith("gemini-"):
        if ChatGoogleGenerativeAI is None:
            raise RuntimeError("langchain-google-genai required for Gemini models")
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY not set")
        return ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key, streaming=streaming)
    if not model_name.startswith(("gpt-", "o1", "o3", "o4")):
        raise ValueError(f"Unrecognized model/provider: {model_name}")
    if ChatOpenAI is None:
        raise RuntimeError("langchain-openai required for OpenAI models")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    return ChatOpenAI(model=model_name, api_key=api_key, streaming=streaming)


async def call_llm(
    prompt: str,
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Any]] = None,
    output_model: Optional[Type[T]] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    **kwargs
) -> Any:
    """
    Call LLM with robust error handling and optional structured output.
    
    Args:
        prompt: User prompt
        model: Model name (uses config default if not provided)
        system_prompt: System prompt (uses config default if not provided)
        tools: Optional list of tools to include in system prompt
        output_model: Optional Pydantic model for structured output
        max_tokens: Max tokens to generate
        temperature: Sampling temperature
        **kwargs: Additional API parameters
        
    Returns:
        String response or parsed output_model instance
        
    Raises:
        LLMError: On API failures after retries
        LLMParseError: If output_model parsing fails
        
    Examples:
        # Simple call
        response = await call_llm("What is 2+2?")
        
        # With structured output
        class Answer(BaseModel):
            result: int
        
        answer = await call_llm(
            "What is 2+2? Respond with JSON.",
            output_model=Answer
        )
        print(answer.result)  # 4
    """
    logger = structlog.get_logger(__name__)
    config = get_llm_config()
    
    # Apply defaults
    model = model or config.default_model
    system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
    max_tokens = max_tokens or config.default_max_tokens
    temperature = config.default_temperature if temperature is None else temperature
    
    # Build system prompt with tools
    enhanced_system_prompt = _build_system_prompt_with_tools(system_prompt, tools)
    
    # Enhance prompt for structured output
    if output_model:
        schema_str = json.dumps(output_model.model_json_schema(), indent=2)
        enhanced_prompt = (
            f"{prompt}\n\n"
            "IMPORTANT: Respond with valid JSON only. No markdown, no explanations, "
            "just the JSON object.\n"
            f"Follow this exact schema:\n```json\n{schema_str}\n```"
        )
    else:
        enhanced_prompt = prompt
    
    logger.info(
        "llm_call_start",
        model=model,
        prompt_length=len(enhanced_prompt),
        has_tools=bool(tools),
        structured_output=bool(output_model)
    )
    
    # Define retry decorator
    @retry(
        stop=stop_after_attempt(config.retry_attempts),
        wait=wait_exponential(
            multiplier=1,
            min=config.retry_min_wait,
            max=config.retry_max_wait
        ),
        retry=retry_if_exception_type((LLMRateLimitError, LLMTimeoutError, ConnectionError)),
        reraise=True
    )
    async def _make_request() -> str:
        """Inner function with retry logic."""
        client = await get_llm_client()

        # Build a messages structure that we can pass to different clients.
        # Use HumanMessage/SystemMessage when available, otherwise fall back to
        # role/content dicts which some lower-level SDKs accept.
        if HumanMessage is not None and SystemMessage is not None:
            msg_system = SystemMessage(content=enhanced_system_prompt)
            msg_user = HumanMessage(content=enhanced_prompt)
            messages_for_client = [msg_system, msg_user]
            messages_wrapper = [[msg_system, msg_user]]
        else:
            messages_for_client = [{"role": "system", "content": enhanced_system_prompt}, {"role": "user", "content": enhanced_prompt}]
            messages_wrapper = [[{"role": "system", "content": enhanced_system_prompt}, {"role": "user", "content": enhanced_prompt}]]

        try:
            # Primary: some provider SDKs expose a messages.create API (original code)
            if hasattr(client, 'messages') and hasattr(client.messages, 'create'):
                response = await asyncio.wait_for(
                    client.messages.create(
                        model=model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=enhanced_system_prompt,
                        messages=[{"role": "user", "content": enhanced_prompt}],
                        **kwargs
                    ),
                    timeout=config.timeout
                )

            # Secondary: LangChain ChatModel async generation interfaces
            elif hasattr(client, 'agenerate'):
                # agenerate often expects a list of message-lists
                try:
                    resp = await asyncio.wait_for(
                        client.agenerate(messages_wrapper, **kwargs),
                        timeout=config.timeout
                    )
                except TypeError:
                    # Some versions expect `messages=` kwarg
                    resp = await asyncio.wait_for(
                        client.agenerate(messages=messages_wrapper, **kwargs),
                        timeout=config.timeout
                    )
                response = resp

            elif hasattr(client, 'agenerate_messages'):
                resp = await asyncio.wait_for(
                    client.agenerate_messages(messages_wrapper, **kwargs),
                    timeout=config.timeout
                )
                response = resp

            # Synchronous/async predict fallback
            elif hasattr(client, 'apredict'):
                resp_text = await asyncio.wait_for(
                    client.apredict(enhanced_prompt, **kwargs),
                    timeout=config.timeout
                )
                response = resp_text

            elif hasattr(client, 'predict'):
                # run blocking predict in a thread
                resp_text = await asyncio.wait_for(
                    asyncio.to_thread(lambda: client.predict(enhanced_prompt, **kwargs)),
                    timeout=config.timeout
                )
                response = resp_text

            else:
                raise RuntimeError("LLM client does not expose a supported API for sending messages")

            # Extract text from various response shapes
            content = None
            # If the response is already a string
            if isinstance(response, str):
                content = response

            # LangChain v0.x-style response
            elif hasattr(response, 'generations'):
                try:
                    first = response.generations[0][0]
                    if hasattr(first, 'text'):
                        content = first.text
                    elif hasattr(first, 'message') and hasattr(first.message, 'content'):
                        content = first.message.content
                except Exception:
                    content = str(response)

            # Some SDK responses have `content` attribute or list of content blocks
            elif hasattr(response, 'content'):
                if isinstance(response.content, list):
                    text_blocks = [getattr(block, 'text', str(block)) for block in response.content]
                    content = "".join(text_blocks)
                else:
                    content = str(response.content)

            else:
                # Fallback to string conversion
                content = str(response)

            # Log usage if available
            if hasattr(response, 'usage'):
                usage = response.usage
                logger.info(
                    "llm_usage",
                    input_tokens=getattr(usage, 'input_tokens', 0),
                    output_tokens=getattr(usage, 'output_tokens', 0)
                )

            return content

        except asyncio.TimeoutError:
            raise LLMTimeoutError(f"Request timeout after {config.timeout}s")
        except Exception as e:
            classified = _classify_error(e)
            logger.error(
                "llm_request_failed",
                error=str(e),
                error_type=type(e).__name__,
                classified_type=type(classified).__name__ if classified is not None else None
            )
            raise classified
    
    # Execute with retry
    try:
        content = await _make_request()
        
    except RetryError as e:
        # All retries exhausted
        logger.error("llm_retries_exhausted", attempts=config.retry_attempts)
        raise LLMError(
            f"Failed after {config.retry_attempts} attempts: {e.last_attempt.exception()}"
        )
    
    # Parse structured output if requested
    if output_model:
        try:
            return _parse_structured_output(content, output_model)
        except Exception as e:
            logger.error(
                "structured_output_parsing_failed",
                error=str(e),
                content_preview=content[:200]
            )
            raise LLMParseError(
                f"Failed to parse output as {output_model.__name__}: {str(e)}"
            )
    
    return content

# Use centralized LLMConfig via utils._utils.get_llm_config()


async def call_llm_stream(
    prompt: str,
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Any]] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    **kwargs
) -> AsyncGenerator[str, None]:
    """
    Stream LLM response with error handling.
    
    Args:
        prompt: User prompt
        model: Model name (uses config default if not provided)
        system_prompt: System prompt (uses config default if not provided)
        tools: Optional list of tools to include in system prompt
        max_tokens: Max tokens to generate
        temperature: Sampling temperature
        **kwargs: Additional API parameters
        
    Yields:
        Token strings
        
    Raises:
        LLMError: On streaming failures
        
    Examples:
        async for token in call_llm_stream("Write a story"):
            print(token, end="", flush=True)
    """
    logger = structlog.get_logger(__name__)
    config = get_llm_config()
    
    # Apply defaults
    model = model or config.default_model
    system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
    max_tokens = max_tokens or config.default_max_tokens
    temperature = temperature or config.default_temperature
    
    # Build system prompt with tools
    enhanced_system_prompt = _build_system_prompt_with_tools(system_prompt, tools)
    
    logger.info(
        "llm_stream_start",
        model=model,
        prompt_length=len(prompt),
        has_tools=bool(tools)
    )
    
    client = await get_llm_client()

    # Try provider-specific streaming where available
    try:
        # Prepare message wrapper for LangChain-style calls
        if HumanMessage is not None and SystemMessage is not None:
            msg_system = SystemMessage(content=enhanced_system_prompt)
            msg_user = HumanMessage(content=prompt)
            messages_wrapper = [[msg_system, msg_user]]
        else:
            messages_wrapper = [[{"role": "system", "content": enhanced_system_prompt}, {"role": "user", "content": prompt}]]

        # If LangChain async callback handler is available, attempt true streaming
        if AsyncCallbackHandler is not None and hasattr(client, 'agenerate'):
            q: asyncio.Queue = asyncio.Queue()

            class _QueueHandler(AsyncCallbackHandler):
                async def on_llm_new_token(self, token: str, **kwargs):
                    await q.put(token)

                async def on_llm_end(self, *args, **kwargs):
                    # signal end of stream
                    await q.put(None)

            handler = _QueueHandler()

            async def _run_agenerate():
                try:
                    try:
                        await client.agenerate(messages_wrapper, callbacks=[handler])
                    except TypeError:
                        # some versions expect messages= kwarg
                        await client.agenerate(messages=messages_wrapper, callbacks=[handler])
                except Exception as e:
                    # on error, push sentinel to avoid hanging consumer
                    try:
                        await q.put(None)
                    except Exception:
                        pass
                    raise

            task = asyncio.create_task(_run_agenerate())

            # yield tokens as they arrive
            try:
                while True:
                    token = await q.get()
                    if token is None:
                        break
                    if token:
                        yield token
                # ensure task finished
                await task
                return
            except asyncio.CancelledError:
                task.cancel()
                raise

        # Primary: provider SDK with messages.stream (original approach)
        if hasattr(client, 'messages') and hasattr(client.messages, 'stream'):
            async with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=enhanced_system_prompt,
                messages=[{"role": "user", "content": prompt}],
                **kwargs
            ) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield text

            # Try to log final usage (best-effort)
            try:
                final_message = await stream.get_final_message()
                if hasattr(final_message, 'usage'):
                    usage = final_message.usage
                    logger.info(
                        "llm_stream_complete",
                        input_tokens=getattr(usage, 'input_tokens', 0),
                        output_tokens=getattr(usage, 'output_tokens', 0)
                    )
            except Exception:
                pass

        else:
            # Fallback: streaming not supported by client interface. Call non-streaming
            # and yield the full content as a single chunk so callers still get a result.
            logger.debug("llm_stream_fallback_to_non_streaming", model=model)
            content = await call_llm(prompt, model=model, system_prompt=system_prompt, tools=tools, max_tokens=max_tokens, temperature=temperature, **kwargs)
            if content:
                yield content

    except asyncio.CancelledError:
        logger.info("llm_stream_cancelled")
        raise
    except Exception as e:
        logger.error("llm_stream_failed", error=str(e), error_type=type(e).__name__)
        raise LLMError(f"Streaming failed: {str(e)}")


def _parse_structured_output(content: str, output_model: Type[T]) -> T:
    """
    Parse structured output with multiple fallback strategies.
    
    Args:
        content: Raw LLM output
        output_model: Target Pydantic model
        
    Returns:
        Parsed model instance
        
    Raises:
        Exception: If all parsing strategies fail
    """
    strategies = [
        _parse_direct,
        _parse_strip_markdown,
        _parse_extract_json,
        _parse_repair_json,
    ]
    
    last_error = None
    for strategy in strategies:
        try:
            return strategy(content, output_model)
        except Exception as e:
            last_error = e
            continue
    
    # All strategies failed
    raise last_error or ValueError("All parsing strategies failed")


def _parse_direct(content: str, model: Type[T]) -> T:
    """Parse directly as JSON."""
    data = json.loads(content.strip())
    return model.model_validate(data)


def _parse_strip_markdown(content: str, model: Type[T]) -> T:
    """Strip markdown code fences and parse."""
    cleaned = content.strip()
    
    # Remove ```json and ``` markers
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first line if it's a fence
        if lines[0].strip().startswith("```"):
            lines = lines[1:]
        # Remove last line if it's a fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)
    
    data = json.loads(cleaned.strip())
    return model.model_validate(data)


def _parse_extract_json(content: str, model: Type[T]) -> T:
    """Extract JSON object from text."""
    import re
    
    # Look for JSON object
    json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
    matches = re.findall(json_pattern, content, re.DOTALL)
    
    if not matches:
        raise ValueError("No JSON object found in content")
    
    # Try each match (in case there are multiple)
    for match in matches:
        try:
            data = json.loads(match)
            return model.model_validate(data)
        except Exception:
            continue
    
    raise ValueError("No valid JSON object found")


def _parse_repair_json(content: str, model: Type[T]) -> T:
    """Attempt to repair common JSON issues."""
    import re
    
    # Extract potential JSON
    json_match = re.search(r'\{.*\}', content, re.DOTALL)
    if not json_match:
        raise ValueError("No JSON-like content found")
    
    json_str = json_match.group(0)
    
    # Common repairs
    repairs = [
        (r"'", '"'),  # Single to double quotes
        (r',\s*}', '}'),  # Trailing commas in objects
        (r',\s*]', ']'),  # Trailing commas in arrays
        (r':\s*None', ': null'),  # Python None to JSON null
        (r':\s*True', ': true'),  # Python True to JSON true
        (r':\s*False', ': false'),  # Python False to JSON false
    ]
    
    for pattern, replacement in repairs:
        json_str = re.sub(pattern, replacement, json_str)
    
    data = json.loads(json_str)
    return model.model_validate(data)
