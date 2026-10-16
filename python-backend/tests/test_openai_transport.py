"""Actual official SDK HTTP/SSE requests; no paid calls or Ollama service."""
import asyncio
import json
import httpx
import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel
from dexter_py.model import llm
from dexter_py.model.openai_client import OpenAIChatClient
from dexter_py.utils import _utils


def completion(text='OK', *, finish='stop', refusal=None):
    return {'id': 'chatcmpl-fixture', 'object': 'chat.completion', 'created': 1,
            'model': 'fixture', 'choices': [{'index': 0, 'finish_reason': finish,
                'message': {'role': 'assistant', 'content': text, 'refusal': refusal}}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 3, 'total_tokens': 15}}


@pytest.mark.parametrize('model,reasoning', [('gpt-4o-mini', False), ('gpt-4.1', False),
                                           ('gpt-5.2', True), ('o3', True),
                                           ('ft:gpt-4o-mini:org:custom:abc', False)])
def test_sdk_wire_parameters_and_authentication(model, reasoning):
    seen = []
    def reply(request):
        seen.append(request)
        return httpx.Response(200, json=completion())
    async def check():
        http = httpx.AsyncClient(transport=httpx.MockTransport(reply))
        client = OpenAIChatClient(model, 'fixture-openai-secret', http_client=http)
        try:
            response = await client.agenerate([[SystemMessage(content='trusted'), HumanMessage(content='hello')]],
                                               max_tokens=137, temperature=0)
            assert response.content == 'OK'
            assert response.usage.input_tokens == 12 and response.usage.output_tokens == 3
            body = json.loads(seen[0].content)
            assert seen[0].url == 'https://api.openai.com/v1/chat/completions'
            assert seen[0].headers['authorization'] == 'Bearer fixture-openai-secret'
            assert body['model'] == model and body['max_completion_tokens'] == 137
            assert 'max_tokens' not in body
            assert ('temperature' not in body) if reasoning else body['temperature'] == 0
            assert body['messages'][0]['role'] == ('developer' if reasoning else 'system')
        finally:
            await client.aclose()
            assert http.is_closed
    asyncio.run(check())


@pytest.mark.parametrize('ending', ['stop', 'length', None])
def test_sdk_stream_requires_valid_completion_marker(ending):
    def reply(request):
        frames = [{'id': 'chatcmpl-fixture', 'object': 'chat.completion.chunk', 'created': 1,
                   'model': 'gpt-4o-mini', 'choices': [{'index': 0, 'delta': {'content': 'hello'}, 'finish_reason': None}]}]
        if ending:
            frames.append({'id': 'chatcmpl-fixture', 'object': 'chat.completion.chunk', 'created': 1,
                           'model': 'gpt-4o-mini', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': ending}]})
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                              text=''.join('data: '+json.dumps(frame)+'\n\n' for frame in frames)+'data: [DONE]\n\n')
    async def check():
        client = OpenAIChatClient('gpt-4o-mini', 'fixture', http_client=httpx.AsyncClient(transport=httpx.MockTransport(reply)))
        try:
            if ending == 'stop':
                assert [chunk.content async for chunk in client.astream([{'role': 'user', 'content': 'hello'}])] == ['hello']
            else:
                with pytest.raises(_utils.LLMError):
                    _ = [chunk async for chunk in client.astream([{'role': 'user', 'content': 'hello'}])]
        finally:
            await client.aclose()
    asyncio.run(check())


@pytest.mark.parametrize('status,attempts', [(401, 1), (429, 3), (503, 3)])
def test_sdk_and_shared_wrapper_do_not_multiply_retries(monkeypatch, status, attempts):
    calls = []
    def reply(request):
        calls.append(request)
        return httpx.Response(status, json={'error': {'message': 'fixture failure', 'type': 'fixture'}})
    async def check():
        client = OpenAIChatClient('gpt-4o-mini', 'fixture', http_client=httpx.AsyncClient(transport=httpx.MockTransport(reply)))
        async def get_client(model):
            return client
        monkeypatch.setattr(llm, 'get_llm_client', get_client)
        monkeypatch.setattr(llm, 'get_llm_config', lambda: _utils.LLMConfig(retry_attempts=3, retry_min_wait=0, retry_max_wait=0))
        try:
            with pytest.raises(_utils.LLMError):
                await llm.call_llm('hello')
            assert len(calls) == attempts
        finally:
            await client.aclose()
    asyncio.run(check())


def test_openai_structured_output_uses_real_sdk_without_ollama(monkeypatch):
    class Answer(BaseModel):
        text: str
    async def check():
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(
            200, json=completion('{"text":"source backed"}')
        )))
        client = OpenAIChatClient('gpt-4o-mini', 'fixture', http_client=http)
        async def get_client(model):
            return client
        monkeypatch.setattr(llm, 'get_llm_client', get_client)
        monkeypatch.setattr(llm, 'ChatOllama', None)
        try:
            parsed = await llm.call_llm('Return an answer.', model='openai/gpt-4o-mini', output_model=Answer)
            assert parsed.text == 'source backed'
        finally:
            await client.aclose()
    asyncio.run(check())


def test_type_error_does_not_issue_duplicate_model_call(monkeypatch):
    count = []
    class Broken:
        async def agenerate(self, *args, **kwargs):
            count.append(1)
            raise TypeError('Internal adapter defect')
    async def get_client(model):
        return Broken()
    monkeypatch.setattr(llm, 'get_llm_client', get_client)
    with pytest.raises(TypeError):
        asyncio.run(llm.call_llm('hello'))
    assert len(count) == 1


def test_provider_rejection_does_not_expose_raw_error_text(monkeypatch):
    async def check():
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            401, json={'error': {'message': 'incorrect API key fixture-private-key', 'type': 'invalid_api_key'}}
        )))
        client = OpenAIChatClient('gpt-4o-mini', 'fixture', http_client=http)
        async def get_client(model):
            return client
        monkeypatch.setattr(llm, 'get_llm_client', get_client)
        try:
            with pytest.raises(_utils.LLMError, match='HTTP 401') as caught:
                await llm.call_llm('hello')
            assert 'fixture-private-key' not in str(caught.value)
        finally:
            await client.aclose()
    asyncio.run(check())


@pytest.mark.parametrize('finish,refusal,text', [('length', None, 'incomplete'),
                                               ('stop', 'cannot comply', None),
                                               ('stop', None, '')])
def test_refused_empty_or_truncated_completion_is_not_success(finish, refusal, text):
    async def check():
        client = OpenAIChatClient('gpt-4o-mini', 'fixture', http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=completion(text, finish=finish, refusal=refusal)))
        ))
        try:
            with pytest.raises(_utils.LLMError):
                await client.agenerate([[{'role': 'user', 'content': 'hello'}]])
        finally:
            await client.aclose()
    asyncio.run(check())


def test_structured_validation_error_does_not_echo_private_model_data(monkeypatch):
    class Answer(BaseModel):
        text: str
    async def check():
        client = OpenAIChatClient('gpt-4o-mini', 'fixture', http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(
                200, json=completion('{"text":{"private":"fixture-sensitive-data"}}')
            ))
        ))
        async def get_client(model):
            return client
        monkeypatch.setattr(llm, 'get_llm_client', get_client)
        try:
            with pytest.raises(_utils.LLMParseError) as caught:
                await llm.call_llm('hello', output_model=Answer)
            assert 'fixture-sensitive-data' not in str(caught.value)
        finally:
            await client.aclose()
    asyncio.run(check())
