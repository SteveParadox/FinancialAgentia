import os
import pytest
from dexter_py.model import llm


def test_get_chat_model_no_api_key(monkeypatch):
    # Ensure OPENAI_API_KEY is not set
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        llm.get_chat_model()


def test_langchain_receives_output_token_budget_and_zero_temperature(monkeypatch):
    import asyncio
    seen = []
    class Client:
        async def agenerate(self, messages, **kwargs):
            seen.append(kwargs)
            return 'bounded'
    async def get_client(model):
        return Client()
    monkeypatch.setattr(llm, 'get_llm_client', get_client)
    assert asyncio.run(llm.call_llm('Research', max_tokens=37, temperature=0)) == 'bounded'
    assert seen[0]['max_tokens'] == 37 and seen[0]['temperature'] == 0


def test_streaming_receives_output_token_budget(monkeypatch):
    import asyncio
    seen = []
    class Client:
        async def astream(self, messages, **kwargs):
            seen.append(kwargs)
            yield 'bounded'
    async def get_client(model):
        return Client()
    monkeypatch.setattr(llm, 'get_llm_client', get_client)
    async def check():
        return [token async for token in llm.call_llm_stream('Research', max_tokens=41, temperature=0)]
    assert asyncio.run(check()) == ['bounded']
    assert seen[0]['max_tokens'] == 41 and seen[0]['temperature'] == 0
