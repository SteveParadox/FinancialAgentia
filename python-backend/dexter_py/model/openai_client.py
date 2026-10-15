"""Official OpenAI SDK transport behind the existing agent completion interface.

Owns HTTP resources and disables SDK retries: the shared LLM layer owns retry
budgets. No Ollama service or LangChain OpenAI adapter is involved.
"""
from __future__ import annotations

import os
import re
from types import SimpleNamespace
from typing import Any

import httpx
from openai import AsyncOpenAI

from ..utils._utils import LLMError


def reasoning_model(model: str) -> bool:
    base = model.split(":", 2)[1] if model.startswith("ft:") and ":" in model else model
    return bool(re.match(r"(?:o[1-9](?:-|$)|gpt-[5-9](?:[.-]|$))", base))


class OpenAIChatClient:
    def __init__(self, model: str, api_key: str, *, timeout: float = 120,
                 http_client: httpx.AsyncClient | None = None):
        if not api_key or not api_key.strip():
            raise RuntimeError("OPENAI_API_KEY not set")
        self.model = model
        self._http = http_client or httpx.AsyncClient(
            timeout=timeout, proxy=os.getenv("HTTPS_PROXY") or os.getenv("https_proxy"),
            follow_redirects=False,
        )
        self.client = AsyncOpenAI(
            api_key=api_key.strip(), base_url="https://api.openai.com/v1",
            timeout=timeout, max_retries=0, http_client=self._http,
        )

    def _request(self, messages: list[Any], options: dict) -> dict:
        output_limit = options.pop("max_tokens", 4096)
        if type(output_limit) is not int or not 1 <= output_limit <= 32768:
            raise ValueError("OpenAI output budget must be between 1 and 32768 tokens")
        temperature = options.pop("temperature", None)
        allowed = {"response_format"}
        if set(options) - allowed:
            raise ValueError("Unsupported OpenAI request options")
        converted = []
        for message in messages:
            if isinstance(message, dict):
                role, content = message.get("role"), message.get("content")
            else:
                role = {"human": "user", "ai": "assistant", "system": "system"}.get(getattr(message, "type", ""))
                content = getattr(message, "content", None)
            if role not in {"system", "developer", "user", "assistant"} or not isinstance(content, str):
                raise ValueError("OpenAI research messages must be typed text messages")
            if role == "system" and reasoning_model(self.model):
                role = "developer"
            converted.append({"role": role, "content": content})
        request = {"model": self.model, "messages": converted,
                   "max_completion_tokens": output_limit, **options}
        # Conservative compatibility: omit sampling overrides for reasoning
        # families. Their defaults apply; classic chat models retain temperature.
        if temperature is not None and not reasoning_model(self.model):
            request["temperature"] = temperature
        return request

    async def agenerate(self, message_batches: list[list[Any]], **options):
        if len(message_batches) != 1:
            raise ValueError("Exactly one research completion per call")
        response = await self.client.chat.completions.create(
            **self._request(message_batches[0], options), stream=False,
        )
        if not response.choices:
            raise LLMError("OpenAI returned no completion choice")
        choice = response.choices[0]
        if choice.finish_reason != "stop" or choice.message.refusal:
            raise LLMError("OpenAI completion refused or did not finish normally")
        if not isinstance(choice.message.content, str) or not choice.message.content.strip():
            raise LLMError("OpenAI returned an empty completion")
        usage = response.usage
        return SimpleNamespace(content=choice.message.content, usage=(
            SimpleNamespace(input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens)
            if usage else None
        ))

    async def astream(self, messages: list[Any], **options):
        stream = await self.client.chat.completions.create(
            **self._request(messages, options), stream=True,
        )
        finished = False
        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if getattr(choice.delta, "refusal", None):
                    raise LLMError("OpenAI refused the research completion")
                if choice.delta.content:
                    yield SimpleNamespace(content=choice.delta.content)
                if choice.finish_reason:
                    if choice.finish_reason != "stop":
                        raise LLMError("OpenAI stream did not finish normally")
                    finished = True
            if not finished:
                raise LLMError("OpenAI stream ended before a completion marker")
        finally:
            await stream.close()

    async def aclose(self):
        await self.client.close()
