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
