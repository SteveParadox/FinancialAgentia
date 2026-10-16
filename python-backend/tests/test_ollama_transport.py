"""Exercise genuine HTTP JSON/NDJSON mapping, not a fake LangChain constructor."""
import asyncio
import json
import httpx
import pytest

from dexter_py.model.ollama_client import OllamaChatClient
from dexter_py.utils._utils import LLMError, LLMRateLimitError


def run(coro):
    return asyncio.run(coro)


def test_local_ollama_bounds_output_and_ignores_unrelated_proxy_environment(monkeypatch):
    monkeypatch.setenv('ALL_PROXY', 'socks5h://unrelated.invalid:9999')
    requests = []
    async def scenario():
        default_client = OllamaChatClient('local-model', 'http://localhost:11434')
        await default_client.aclose()
        def reply(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'done': True, 'done_reason': 'stop',
                                           'message': {'role': 'assistant', 'content': '{"ok":true}'},
                                           'prompt_eval_count': 12, 'eval_count': 5})
        client = OllamaChatClient('local-model', 'http://localhost:11434', http_client=httpx.AsyncClient(transport=httpx.MockTransport(reply)))
        result = await client.agenerate([[{'role': 'user', 'content': 'test'}]], max_tokens=128, temperature=0)
        assert result.content == '{"ok":true}' and result.usage.input_tokens == 12
        assert requests[0]['options'] == {'num_predict': 128, 'temperature': 0}
        assert requests[0]['stream'] is False
        await client.aclose()
    run(scenario())


def test_cloud_credentials_reach_only_configured_https_ollama_endpoint():
    async def scenario():
        requests = []
        def reply(request):
            requests.append(request)
            return httpx.Response(200, json={'done': True, 'message': {'content': 'ok'}})
        client = OllamaChatClient('configured-model', 'https://ollama.com',
                                  client_kwargs={'headers': {'Authorization': 'Bearer fixture-secret'}},
                                  http_client=httpx.AsyncClient(transport=httpx.MockTransport(reply)))
        await client.agenerate([[{'role': 'user', 'content': 'test'}]], max_tokens=128)
        assert requests[0].headers['authorization'] == 'Bearer fixture-secret'
        assert str(requests[0].url) == 'https://ollama.com/api/chat'
        await client.aclose()
        with pytest.raises(ValueError):
            OllamaChatClient('model', 'http://localhost:11434', client_kwargs={'headers': {'Authorization': 'secret'}})
    run(scenario())


@pytest.mark.parametrize('mode', ['valid', 'missing_terminal', 'truncated', 'invalid'])
def test_ndjson_stream_is_validated_and_requires_normal_terminal_state(mode):
    async def scenario():
        frames = [{'done': False, 'message': {'content': 'hello'}}]
        if mode != 'missing_terminal':
            frames.append({'done': True, 'done_reason': 'length' if mode == 'truncated' else 'stop', 'message': {'content': ''}})
        body = '\n'.join(json.dumps(row) for row in frames)+'\n' if mode != 'invalid' else '{bad json}\n'
        client = OllamaChatClient('model', 'http://localhost:11434', http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, text=body))))
        try:
            if mode == 'valid':
                assert ''.join([chunk.content async for chunk in client.astream([{'role': 'user', 'content': 'test'}], max_tokens=128)]) == 'hello'
            else:
                with pytest.raises(LLMError):
                    async for _ in client.astream([{'role': 'user', 'content': 'test'}], max_tokens=128):
                        pass
        finally:
            await client.aclose()
    run(scenario())


def test_rate_limits_are_sanitized_and_do_not_leak_provider_body():
    async def scenario():
        client = OllamaChatClient('model', 'http://localhost:11434', http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(429, text='secret reflected in body'))))
        try:
            with pytest.raises(LLMRateLimitError, match='rate limited') as error:
                await client.agenerate([[{'role': 'user', 'content': 'test'}]], max_tokens=128)
            assert 'secret' not in str(error.value)
        finally:
            await client.aclose()
    run(scenario())
