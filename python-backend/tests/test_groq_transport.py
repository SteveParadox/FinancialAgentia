"""Groq provider routing and OpenAI-compatible wire tests (fixture traffic only)."""
import asyncio
import json

import httpx
import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from dexter_py.model import llm
from dexter_py.model.openai_client import OpenAIChatClient


OFFICIAL = "https://api.groq.com/openai/v1"


def success_payload(text="Groq response"):
    return {
        "id": "chatcmpl-fixture", "object": "chat.completion",
        "created": 1, "model": "openai/gpt-oss-120b",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
    }


def test_groq_routes_with_own_key_and_full_nested_model_id(monkeypatch):
    seen = {}
    def factory(**options):
        seen.update(options)
        return object()
    monkeypatch.setattr(llm, "OpenAIChatClient", factory)
    monkeypatch.setenv("GROQ_API_KEY", "fixture-groq-key")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-openai-key")
    monkeypatch.setenv("GROQ_BASE_URL", OFFICIAL + "/")
    model = "groq/openai/gpt-oss-120b"
    assert llm.model_provider(model) == "groq"
    assert llm.model_configuration_ready(model)
    llm.get_chat_model(model)
    assert seen["model"] == "openai/gpt-oss-120b"
    assert seen["api_key"] == "fixture-groq-key"
    assert seen["base_url"] == OFFICIAL


def test_groq_missing_key_does_not_fall_back_to_openai(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-openai-key")
    monkeypatch.setenv("GROQ_BASE_URL", OFFICIAL)
    assert not llm.model_configuration_ready("groq/openai/gpt-oss-120b")
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        llm.get_chat_model("groq/openai/gpt-oss-120b")


@pytest.mark.parametrize("invalid", [
    "", "http://api.groq.com/openai/v1",
    "https://api.groq.com.evil.test/openai/v1",
    "https://api.groq.com:443/openai/v1",
    "https://api.groq.com/openai/v1/chat/completions",
    "https://api.groq.com/openai/v1?x=1",
    "https://api.groq.com/openai/v1#frag",
    "https://user@api.groq.com/openai/v1",
])
def test_groq_rejects_untrusted_base_urls(monkeypatch, invalid):
    monkeypatch.setenv("GROQ_API_KEY", "fixture-groq-key")
    monkeypatch.setenv("GROQ_BASE_URL", invalid)
    assert not llm.model_configuration_ready("groq/openai/gpt-oss-120b")
    with pytest.raises(ValueError, match="GROQ_BASE_URL"):
        llm.get_chat_model("groq/openai/gpt-oss-120b")


@pytest.mark.parametrize("model", ["groq/", "groq/bad model", "groq/  "])
def test_groq_requires_nonempty_whitespace_free_model_id(monkeypatch, model):
    monkeypatch.setenv("GROQ_API_KEY", "fixture-groq-key")
    assert not llm.model_configuration_ready(model)
    with pytest.raises(ValueError):
        llm.get_chat_model(model)


def test_groq_completions_wire_and_no_openai_credentials(monkeypatch):
    captured = []
    def reply(request):
        captured.append(request)
        return httpx.Response(200, json=success_payload())
    original_factory = OpenAIChatClient
    def fixture_factory(**kwargs):
        return original_factory(
            **kwargs,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(reply)),
        )
    monkeypatch.setattr(llm, "OpenAIChatClient", fixture_factory)
    monkeypatch.setenv("GROQ_API_KEY", "fixture-groq-key")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-openai-key")
    monkeypatch.setenv("GROQ_BASE_URL", OFFICIAL)

    async def exercise():
        client = llm.get_chat_model("groq/openai/gpt-oss-120b")
        try:
            result = await client.agenerate(
                [[SystemMessage(content="Research"), HumanMessage(content="Hello")]],
                max_tokens=321, temperature=0,
            )
            assert result.content == "Groq response"
            assert result.usage.input_tokens == 11
        finally:
            await client.aclose()
    asyncio.run(exercise())
    assert len(captured) == 1
    request = captured[0]
    assert str(request.url) == OFFICIAL + "/chat/completions"
    assert request.headers["authorization"] == "Bearer fixture-groq-key"
    assert "fixture-openai-key" not in str(request.headers)
    body = json.loads(request.content)
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["max_completion_tokens"] == 321
    assert body["messages"][-1] == {"role": "user", "content": "Hello"}


def test_groq_streaming_uses_the_same_endpoint(monkeypatch):
    captured = []
    def reply(request):
        captured.append(request)
        frames = [
            {"id": "chunk1", "object": "chat.completion.chunk", "created": 1,
             "model": "openai/gpt-oss-120b",
             "choices": [{"index": 0, "delta": {"content": "Hello"},
                          "finish_reason": None}]},
            {"id": "chunk2", "object": "chat.completion.chunk", "created": 1,
             "model": "openai/gpt-oss-120b",
             "choices": [{"index": 0, "delta": {"content": " world"},
                          "finish_reason": "stop"}]},
        ]
        payload = "".join("data: " + json.dumps(frame) + "\\n\\n" for frame in frames)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            text=payload + "data: [DONE]\\n\\n",
        )
    monkeypatch.setenv("GROQ_API_KEY", "fixture-groq-key")
    monkeypatch.setenv("GROQ_BASE_URL", OFFICIAL)
    original_factory = OpenAIChatClient
    monkeypatch.setattr(
        llm, "OpenAIChatClient",
        lambda **kwargs: original_factory(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(reply))),
    )

    async def exercise():
        client = llm.get_chat_model("groq/openai/gpt-oss-120b")
        try:
            return [part.content async for part in client.astream([
                {"role": "user", "content": "Hello"}], max_tokens=91)]
        finally:
            await client.aclose()
    assert asyncio.run(exercise()) == ["Hello", " world"]
    assert len(captured) == 1
    assert str(captured[0].url) == OFFICIAL + "/chat/completions"
    assert json.loads(captured[0].content)["stream"] is True


def test_openai_provider_still_uses_original_endpoint(monkeypatch):
    seen = []
    def reply(request):
        seen.append(request)
        return httpx.Response(200, json=success_payload("OpenAI response"))
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-openai-key")
    monkeypatch.setenv("GROQ_API_KEY", "fixture-groq-key")
    monkeypatch.setenv("GROQ_BASE_URL", OFFICIAL)

    original_factory = OpenAIChatClient
    monkeypatch.setattr(
        llm, "OpenAIChatClient",
        lambda **kwargs: original_factory(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(reply))),
    )
    async def exercise():
        client = llm.get_chat_model("openai/gpt-4o-mini")
        try:
            await client.agenerate([[{"role": "user", "content": "Hi"}]])
        finally:
            await client.aclose()
    asyncio.run(exercise())
    assert str(seen[0].url) == "https://api.openai.com/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer fixture-openai-key"
