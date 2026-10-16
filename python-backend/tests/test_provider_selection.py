"""Provider routing tests: no network and no fallback to another provider."""
import pytest

from dexter_py.model import llm


def test_openai_does_not_fall_back_to_other_provider(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "other-provider-key")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        llm.get_chat_model("gpt-4o-mini")


def test_unknown_provider_rejected():
    with pytest.raises(ValueError, match="Unrecognized"):
        llm.get_chat_model("unknown-provider-model")


def test_ollama_cloud_passes_authorization_header(monkeypatch):
    values = {}
    def fake_ollama(**kwargs):
        values.update(kwargs)
        return object()
    monkeypatch.setattr(llm, "ChatOllama", fake_ollama)
    monkeypatch.setenv("OLLAMA_API_KEY", "test-cloud-key")
    llm.get_chat_model("ollama-qwen3-coder:480b-cloud")
    assert values["model"] == "qwen3-coder:480b-cloud"
    assert values["client_kwargs"]["headers"]["Authorization"] == "Bearer test-cloud-key"


def test_ollama_cloud_without_key_fails(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "")
    monkeypatch.setattr(llm, "ChatOllama", lambda **kwargs: object())
    with pytest.raises(RuntimeError, match="OLLAMA_API_KEY"):
        llm.get_chat_model("ollama-test-cloud")

def test_llm_client_cache_is_keyed_by_model(monkeypatch):
    import asyncio
    from dexter_py.utils import _utils

    built = []
    def fake_factory(model):
        built.append(model)
        return object()

    monkeypatch.setattr(llm, "get_chat_model", fake_factory)
    _utils.configure_llm(_utils.LLMConfig(default_model="gpt-4o-mini"))
    async def scenario():
        first = await _utils.get_llm_client("gpt-4o-mini")
        second = await _utils.get_llm_client("gpt-4o")
        again = await _utils.get_llm_client("gpt-4o-mini")
        assert first is again
        assert first is not second
        assert built == ["gpt-4o-mini", "gpt-4o"]
    asyncio.run(scenario())
    _utils.configure_llm(_utils.LLMConfig())


def test_streaming_does_not_wait_for_callback_completion(monkeypatch):
    import asyncio
    from dexter_py.utils._utils import LLMConfig

    class Chunk:
        def __init__(self, content):
            self.content = content

    class FakeStreamingModel:
        async def astream(self, messages, **kwargs):
            yield Chunk("hello")
            yield Chunk(" world")

    async def client(model):
        return FakeStreamingModel()

    monkeypatch.setattr(llm, "get_llm_client", client)
    monkeypatch.setattr(llm, "get_llm_config",
                        lambda: LLMConfig(default_model="gpt-4o-mini", timeout=0.2))

    async def scenario():
        result = []
        async for token in llm.call_llm_stream("TEST"):
            result.append(token)
        assert result == ["hello", " world"]

    asyncio.run(scenario())


def test_readiness_requires_provider_sdk_even_with_a_key(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'fixture')
    monkeypatch.setattr(llm, 'ChatAnthropic', None)
    assert not llm.model_configuration_ready('claude-fixture')


@pytest.mark.parametrize('model', ['gpt-4o-mini', 'gpt-5.2', 'o3', 'openai/ft:gpt-4o-mini:org:name:id'])
def test_openai_factory_is_independent_of_ollama(monkeypatch, model):
    captured = {}
    def factory(**options):
        captured.update(options)
        return object()
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-openai-key')
    monkeypatch.delenv('OLLAMA_API_KEY', raising=False)
    monkeypatch.setattr(llm, 'ChatOllama', None)
    monkeypatch.setattr(llm, 'OpenAIChatClient', factory)
    assert llm.model_configuration_ready(model)
    llm.get_chat_model(model)
    assert captured['api_key'] == 'fixture-openai-key'
    assert captured['model'] == model.removeprefix('openai/')
    assert llm.model_provider(model) == 'openai'


def test_readiness_and_factory_reject_whitespace_model_ids(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture')
    assert not llm.model_configuration_ready('openai/gpt 4o')
    with pytest.raises(ValueError, match='whitespace'):
        llm.get_chat_model('openai/gpt 4o')
