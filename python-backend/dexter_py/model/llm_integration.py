# llm_integration.py
"""Integration layer between llm.py and agent phases"""
from typing import Type, TypeVar, Optional, AsyncGenerator, Any
from .llm import call_llm, call_llm_stream, LLMError, LLMParseError
import structlog
import json

logger = structlog.get_logger()

T = TypeVar('T')

class StructuredLLMClient:
    """Wrapper around llm.py with structured output guarantees"""
    
    def __init__(self, model: str):
        self.model = model
    
    async def complete_structured(
        self,
        prompt: str,
        output_model: Type[T],
        system_prompt: Optional[str] = None,
        temperature: float = 0.2,
        max_retries: int = 3
    ) -> T:
        """
        Get structured output with validation and fallback strategies
        """
        for attempt in range(max_retries):
            try:
                # Use the native structured output support in llm.py
                result = await call_llm(
                    prompt=prompt,
                    model=self.model,
                    system_prompt=system_prompt,
                    output_model=output_model,
                    temperature=temperature,
                    max_tokens=4096
                )
                
                if isinstance(result, output_model):
                    return result
                else:
                    # If llm.py returns dict (from old version), parse it
                    return output_model.model_validate(result)
                    
            except (LLMParseError, json.JSONDecodeError, ValidationError) as e:
                logger.warning(
                    "structured_output_parse_failed",
                    attempt=attempt + 1,
                    error=str(e),
                    model=self.model
                )
                if attempt == max_retries - 1:
                    raise
                # Increase temperature slightly on retry to avoid repetition
                temperature = min(temperature + 0.1, 1.0)
            except LLMError as e:
                logger.error("llm_error", error=str(e))
                raise
    
    async def stream_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """Stream text output for answer phase"""
        async for token in call_llm_stream(
            prompt=prompt,
            model=self.model,
            system_prompt=system_prompt
        ):
            yield token

# Import ValidationError from pydantic
from pydantic import ValidationError