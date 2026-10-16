"""Actual API + Redis Lua + worker + provider adapter; only external IO is faked."""
import asyncio
from datetime import datetime, timezone
import json
import os
import uuid

import httpx
import pytest
from jose import jwt

from app import main
from app.execution_worker import ExecutionWorker
from dexter_py.agent.execution_registry import ExecutionRegistry
from dexter_py.agent.financial_evidence import ProposedClaims
from dexter_py.agent.orchestrator import Orchestrator
from dexter_py.agent.schemas import Understanding, Plan, PlanTask, ToolCall, ReflectionOutput
from dexter_py.agent.tool_executor import ToolExecutor
from dexter_py.tools.financial_datasets import FinancialDatasetsClient


class ResearchLLM:
    """Deterministic LLM port; never replaces the orchestrator or worker."""
    broken = False
    no_data = False
    async def complete_structured(self, *, output_model, **kwargs):
        if self.broken:
            raise ValueError('Invalid model JSON')
        if output_model is Understanding:
            return Understanding(intent='AAPL financial research')
        if output_model is Plan:
            return Plan(summary='Market and risk research', tasks=[
                PlanTask(id='market', description='Fetch AAPL', tool_calls=[
                    ToolCall(tool='get_price_snapshot', parameters={'ticker': 'AAPL'})]),
                PlanTask(id='risk', description='Compute risk from daily closes', tool_calls=[
                    ToolCall(tool='calculate_risk_metrics', parameters={
                        'ticker': 'AAPL', 'start_date': '2026-09-01', 'end_date': '2026-09-03'})]),
            ])
        if output_model is ReflectionOutput:
            return ReflectionOutput(is_complete=True, confidence=0.9, reasoning='Data collected')
        if output_model is ProposedClaims:
            return ProposedClaims()  # Deterministic evidence renderer is the fallback.
        raise AssertionError(output_model)

    async def stream_text(self, **kwargs):
        yield 'Unverified model draft claiming a guaranteed windfall.'


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def configured(monkeypatch):
    url = os.getenv('TEST_REDIS_URL')
    if not url:
        pytest.skip('TEST_REDIS_URL required for real durable pipeline tests')
    monkeypatch.setenv('REDIS_URL', url)
    monkeypatch.setenv('EXECUTION_REDIS_PREFIX', 'pipeline-' + uuid.uuid4().hex + ':')
    monkeypatch.setenv('JWT_SECRET', 'fixture-only-secret-for-durable-pipeline')
    monkeypatch.delenv('FINANCIAL_DATASETS_API_KEY', raising=False)
    monkeypatch.setenv('BACKEND_ENV', 'development')
    return url


def auth(who='alice', **extra):
    return {'Authorization': 'Bearer ' + jwt.encode({'sub': who}, os.environ['JWT_SECRET'], algorithm='HS256'), **extra}


async def exercise_setup(callback, mode='success'):
    async with main.app.router.lifespan_context(main.app):
        requests = []
        async def provider_reply(req):
            requests.append(req)
            if mode == 'slow':
                await asyncio.sleep(60)
            if mode == 'missing':
                return httpx.Response(200, json={'snapshot': None, 'prices': []})
            if mode == 'limited':
                return httpx.Response(429, headers={'retry-after': '0'})
            if req.url.path == '/prices/':
                return httpx.Response(200, json={'prices': [
                    {'ticker': 'AAPL', 'currency': 'USD', 'time': f'2026-09-0{i}T20:00:00Z', 'close': close}
                    for i, close in enumerate([100, 110, 99], 1)
                ]})
            return httpx.Response(200, json={'snapshot': {'ticker': 'AAPL', 'price': 100,
                                  'currency': 'USD', 'time': '2026-09-03T20:00:00Z'}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider_reply)) as provider_http:
            provider = FinancialDatasetsClient('fixture-secret', http_client=provider_http)
            tools = ToolExecutor()
            tools.register('get_price_snapshot', lambda ticker: None)
            async def quote(ticker):
                return await provider.retrieve('get_price_snapshot', ticker)
            tools.register('get_price_snapshot', quote)
            tools.register('calculate_risk_metrics', provider.risk_metrics)
            llm = ResearchLLM()
            if mode == 'llm':
                llm.broken = True
            orchestrator = Orchestrator('gpt-4o-mini', max_iterations=1, llm_client=llm, tool_executor=tools)
            main.app.state.orchestrator = orchestrator
            worker = ExecutionWorker(main.app.state.execution_registry, orchestrator, lease_seconds=5)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test', trust_env=False) as api:
                await callback(api, worker, requests)
        # Clean only this test's unique namespace, never flush shared databases.
        registry = main.app.state.execution_registry
        keys = [key async for key in registry.redis.scan_iter(match=registry.prefix + '*')]
        if keys:
            await registry.redis.delete(*keys)


def test_admission_worker_evidence_persistence_history_replay_and_ownership(configured):
    async def scenario(api, worker, requests):
        payload = {'query': 'Research AAPL price and risk'}
        admission_headers = auth(**{'Idempotency-Key': 'same-request-123'})
        responses = await asyncio.gather(*(api.post('/api/v1/agents/executions', json=payload,
                                                    headers=admission_headers) for _ in range(8)))
        assert all(r.status_code == 202 for r in responses), [r.text for r in responses]
        jobs = [r.json() for r in responses]
        assert len({j['execution_id'] for j in jobs}) == len({j['session_id'] for j in jobs}) == 1
        assert sum(j['created'] for j in jobs) == 1
        conflict = await api.post('/api/v1/agents/executions', json={'query': 'MSFT'}, headers=admission_headers)
        assert conflict.status_code == 409
        job = jobs[0]['execution_id']
        base = '/api/v1/agents/executions/' + job
        assert (await api.get(base, headers=auth('bob'))).status_code == 404
        assert (await api.post(base + '/cancel', headers=auth('bob'))).status_code == 404
        assert (await api.get(base + '/events', headers=auth('bob'))).status_code == 404
        assert (await api.get('/api/v1/agents/executions', headers=auth('bob'))).json()['executions'] == []
        claimed = await worker.registry.claim(worker.worker_id, 5)
        assert claimed and await worker.process(*claimed) == 'SUCCEEDED'
        record = (await api.get(base, headers=auth())).json()
        report = record['result']
        assert report['schema_version'] == 1
        assert 'guaranteed' not in report['answer']
        assert 'Limitations:' in report['answer'] and 'Sources:' in report['answer']
        assert len(report['agent_executions']) == 2
        drawdown = next(e for e in report['evidence'] if e['metric'] == 'max_drawdown')
        assert float(drawdown['value']) == pytest.approx(-0.1)
        assert drawdown['input_evidence_ids']
        assert len(requests) == 2
        principal = worker.registry.principal('jwt::alice')
        persisted = await worker.registry.get(job, principal, include_artifacts=True)
        assert persisted['artifacts']['plans'][0]['tasks'][0]['status'] == 'completed'
        history = (await api.get('/api/v1/agents/executions', headers=auth())).json()['executions']
        assert history[0]['execution_id'] == job and 'result' not in history[0]
        events = await api.get(base + '/events', headers=auth())
        assert events.headers['x-accel-buffering'] == 'no'
        ids = [line[4:] for line in events.text.splitlines() if line.startswith('id: ')]
        assert ids == sorted(ids, key=lambda x: tuple(map(int, x.split('-'))))
        replay = await api.get(base + '/events', headers=auth(**{'Last-Event-ID': ids[-1]}))
        assert 'id: ' not in replay.text and '"type": "answer"' in replay.text
        assert (await api.get(base + '/events', headers=auth(**{'Last-Event-ID': 'invalid'}))).status_code == 400
    run(exercise_setup(scenario))


@pytest.mark.parametrize('mode', ['missing', 'limited', 'llm'])
def test_external_failures_do_not_publish_fabricated_success(configured, mode):
    async def scenario(api, worker, requests):
        response = await api.post('/api/v1/agents/executions', json={'query': 'AAPL'},
                                  headers=auth(**{'Idempotency-Key': 'failure-request-' + mode}))
        job = response.json()['execution_id']
        claim = await worker.registry.claim(worker.worker_id, 5)
        assert await worker.process(*claim) == 'FAILED'
        status = (await api.get('/api/v1/agents/executions/' + job, headers=auth())).json()
        assert status['state'] == 'FAILED' and not status.get('result')
        if mode == 'limited':
            assert len(requests) == 6  # Three bounded attempts per actual tool.
    run(exercise_setup(scenario, mode))


def test_running_cancellation_stops_work_without_success_or_history(configured):
    async def scenario(api, worker, requests):
        response = await api.post('/api/v1/agents/executions', json={'query': 'AAPL'},
                                  headers=auth(**{'Idempotency-Key': 'cancel-during-work'}))
        job = response.json()['execution_id']
        claim = await worker.registry.claim(worker.worker_id, 5)
        processing = asyncio.create_task(worker.process(*claim))
        for _ in range(100):
            if requests:
                break
            await asyncio.sleep(0.01)
        assert requests
        assert (await api.post('/api/v1/agents/executions/' + job + '/cancel', headers=auth())).json()['cancel_accepted']
        assert await asyncio.wait_for(processing, 3) == 'CANCELLED'
        final = await worker.registry.get(job, worker.registry.principal('jwt::alice'))
        assert final['state'] == 'CANCELLED' and 'result' not in final
    run(exercise_setup(scenario, 'slow'))


def test_readiness_handles_dependency_outage_as_503(configured, monkeypatch):
    async def scenario(api, worker, requests):
        async def offline():
            raise ConnectionError('Redis unavailable')
        monkeypatch.setattr(worker.registry, 'health', offline)
        ready = await api.get('/ready')
        assert ready.status_code == 503
        assert ready.json()['durable_execution_registry_ready'] is False
        assert (await api.get('/health')).status_code == 200
    run(exercise_setup(scenario))


def test_worker_rejects_changed_model_instead_of_running_wrong_provider(configured):
    async def scenario(api, worker, requests):
        response = await api.post('/api/v1/agents/executions', json={'query': 'AAPL'},
                                  headers=auth(**{'Idempotency-Key': 'model-config-mismatch'}))
        job = response.json()['execution_id']
        claim = await worker.registry.claim(worker.worker_id, 5)
        worker.orchestrator.model = 'openai/gpt-4.1'
        assert await worker.process(*claim) == 'FAILED'
        final = (await api.get('/api/v1/agents/executions/' + job, headers=auth())).json()
        assert final['failure_code'] == 'MODEL_CONFIGURATION_CHANGED'
        assert not requests and 'result' not in final
    run(exercise_setup(scenario))


def test_authenticated_backend_model_configuration_is_truthful(configured, monkeypatch):
    async def scenario(api, worker, requests):
        monkeypatch.setenv('OPENAI_API_KEY', 'fixture-openai-secret')
        monkeypatch.delenv('OLLAMA_API_KEY', raising=False)
        assert (await api.get('/api/v1/model')).status_code == 401
        response = await api.get('/api/v1/model', headers=auth())
        assert response.json() == {'protocol_version': 1, 'provider': 'openai',
                                   'model': 'gpt-4o-mini', 'configured': True}
        assert 'fixture-openai-secret' not in response.text and not requests
    run(exercise_setup(scenario))


def test_complete_research_uses_openai_sdk_http_without_ollama(configured, monkeypatch):
    from dexter_py.model import llm
    from dexter_py.model.openai_client import OpenAIChatClient
    from test_openai_transport import completion

    async def scenario(api, worker, provider_requests):
        model_requests = []
        async def model_reply(request):
            body = json.loads(request.content)
            model_requests.append(body)
            assert request.url.host == 'api.openai.com'
            assert body['model'] == 'gpt-4.1' and body['max_completion_tokens'] == 4096
            if body.get('stream'):
                frames = [{'id': 'chatcmpl-fixture', 'object': 'chat.completion.chunk', 'created': 1,
                           'model': 'gpt-4.1', 'choices': [{'index': 0, 'delta': {'content': 'Untrusted draft.'}, 'finish_reason': None}]},
                          {'id': 'chatcmpl-fixture', 'object': 'chat.completion.chunk', 'created': 1,
                           'model': 'gpt-4.1', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}]
                return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                                      text=''.join('data: '+json.dumps(frame)+'\n\n' for frame in frames)+'data: [DONE]\n\n')
            schema = json.loads(body['messages'][-1]['content'].rsplit('```json\n', 1)[-1].split('\n```')[0])
            model = {'Understanding': Understanding, 'Plan': Plan, 'ReflectionOutput': ReflectionOutput,
                     'ProposedClaims': ProposedClaims}[schema['title']]
            result = await ResearchLLM().complete_structured(output_model=model)
            return httpx.Response(200, json=completion(result.model_dump_json()))

        client = OpenAIChatClient('gpt-4.1', 'fixture-openai-secret',
                                  http_client=httpx.AsyncClient(transport=httpx.MockTransport(model_reply)))
        async def get_client(model):
            return client
        monkeypatch.setattr(llm, 'get_llm_client', get_client)
        monkeypatch.setattr(llm, 'ChatOllama', None)
        monkeypatch.delenv('OLLAMA_API_KEY', raising=False)
        tools = worker.orchestrator.execute_phase.tool_executor
        worker.orchestrator = Orchestrator('openai/gpt-4.1', max_iterations=1, tool_executor=tools)
        main.app.state.orchestrator = worker.orchestrator
        try:
            response = await api.post('/api/v1/agents/executions', json={'query': 'Research AAPL'},
                                      headers=auth(**{'Idempotency-Key': 'official-openai-pipeline'}))
            job = response.json()['execution_id']
            claim = await worker.registry.claim(worker.worker_id, 5)
            assert await worker.process(*claim) == 'SUCCEEDED'
            final = (await api.get('/api/v1/agents/executions/' + job, headers=auth())).json()
            assert 'AAPL price: 100' in final['result']['answer']
            assert len(model_requests) == 5 and len(provider_requests) == 2
            assert final['result']['evidence'] and final['result']['sources']
        finally:
            await client.aclose()
    run(exercise_setup(scenario))
