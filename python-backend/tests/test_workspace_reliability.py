"""Regression tests against the real API, authoritative Redis and worker."""
import asyncio
import json
import uuid

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from test_durable_pipeline import configured, exercise_setup, auth, run, ResearchLLM
from app import main
from app.execution_worker import ExecutionWorker
from dexter_py.agent.orchestrator import Orchestrator
from dexter_py.agent.schemas import Plan, ReflectionOutput, Understanding
from dexter_py.model.budget import ModelBudget, ModelBudgetExceeded, current_budget, reserve_model_call


def test_retry_is_authorized_atomic_single_successor_and_preserves_failed_attempt(configured):
    async def scenario(api, worker, requests):
        base = '/api/v1/agents/executions'
        created = await api.post(base, json={'query': 'Research AAPL'}, headers=auth(**{'Idempotency-Key': 'source-job-123'}))
        job = created.json()['execution_id']
        assert (await api.post(f'{base}/{job}/retry', headers=auth(**{'Idempotency-Key': 'premature-retry'}))).status_code == 409
        claim = await worker.registry.claim(worker.worker_id, 5)
        assert await worker.registry.finish(*[claim[0], worker.worker_id, claim[1]], 'FAILED', failure_code='PROVIDER_TIMEOUT') == 'FAILED'
        assert (await api.post(f'{base}/{job}/retry', headers=auth('bob', **{'Idempotency-Key': 'unauthorized-retry'}))).status_code == 404
        assert (await api.get(f'{base}/{job}/result', headers=auth('bob'))).status_code == 404
        assert (await api.get(f'{base}/{job}/result', headers=auth())).status_code == 409
        headers = auth(**{'Idempotency-Key': 'single-retry-token'})
        replies = await asyncio.gather(*(api.post(f'{base}/{job}/retry', headers=headers) for _ in range(8)))
        assert all(reply.status_code == 202 for reply in replies), [reply.text for reply in replies]
        assert sum(reply.json()['created'] for reply in replies) == 1
        child = replies[0].json()['execution_id']
        assert child != job
        assert (await api.post(f'{base}/{job}/retry', headers=auth(**{'Idempotency-Key': 'different-retry-token'}))).status_code == 409
        original = (await api.get(f'{base}/{job}', headers=auth())).json()
        assert original['state'] == 'FAILED' and original['retry_execution_id'] == child
        successor = (await api.get(f'{base}/{child}', headers=auth())).json()
        assert successor['parent_execution_id'] == job and successor['query'] == 'Research AAPL'
        assert await worker.process(*(await worker.registry.claim(worker.worker_id, 5))) == 'SUCCEEDED'
        saved = (await api.get(f'{base}/{child}/result', headers=auth())).json()
        assert saved['execution_id'] == child and saved['evidence']
    run(exercise_setup(scenario))


def test_scope_is_validated_fingerprinted_and_missing_instruments_are_disclosed(configured):
    async def scenario(api, worker, requests):
        base = '/api/v1/agents/executions'
        headers = auth(**{'Idempotency-Key': 'typed-scope-request'})
        payload = {'query': 'Compare AAPL and MSFT', 'scope': {'category': 'comparison', 'symbols': ['AAPL', 'MSFT']}}
        assert (await api.post(base, json={**payload, 'scope': {'category': 'comparison', 'symbols': ['AAPL']}}, headers=headers)).status_code == 422
        response = await api.post(base, json=payload, headers=headers)
        assert response.status_code == 202
        other = {**payload, 'scope': {'category': 'comparison', 'symbols': ['AAPL', 'GOOGL']}}
        assert (await api.post(base, json=other, headers=headers)).status_code == 409
        job = response.json()['execution_id']
        assert await worker.process(*(await worker.registry.claim(worker.worker_id, 5))) == 'SUCCEEDED'
        report = (await api.get(f'{base}/{job}/result', headers=auth())).json()
        assert report['status'] == 'PARTIAL'
        assert any('MSFT' in warning for warning in report['limitations'])
        assert all(row['symbol'] == 'AAPL' for row in report['evidence'])
    run(exercise_setup(scenario))


def test_recovery_reuses_completed_plan_and_original_evidence(configured):
    async def scenario(api, worker, requests):
        class CrashAfterTools(Exception):
            pass
        llm = ResearchLLM()
        agent = Orchestrator('gpt-4o-mini', max_iterations=1, llm_client=llm,
                             tool_executor=worker.orchestrator.execute_phase.tool_executor)
        snapshots = []
        async def crash_at_boundary(snapshot):
            snapshots.append(snapshot)
            if snapshot['plans']:
                raise CrashAfterTools()
        with pytest.raises(CrashAfterTools):
            await agent.run('AAPL', checkpoint_callback=crash_at_boundary)
        assert len(requests) == 2
        restored = []
        async def reject_replanning(*args, **kwargs):
            raise AssertionError('Recovered completed tools must not be repeated')
        agent._understand = reject_replanning
        agent._plan = reject_replanning
        agent._execute = reject_replanning
        answer = await agent.run('AAPL', resume=snapshots[-1], evidence_collector=restored.append,
                                 execution_identity='stable-job:2')
        assert answer and len(restored) == 2 and len(requests) == 2
        # Checkpoint data is validated rather than accepting arbitrary dictionaries.
        invalid = {**snapshots[-1], 'model_version': 'different'}
        with pytest.raises(ValueError):
            await agent.run('AAPL', resume=invalid)
    run(exercise_setup(scenario))


def test_worker_polling_recovers_after_temporary_redis_failure(configured):
    async def scenario(api, worker, requests):
        original = worker.registry.claim
        calls = 0
        async def fail_once(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RedisConnectionError('fixture storage outage')
            worker.stopping.set()
            return None
        worker.registry.claim = fail_once
        worker.poll_seconds = .01
        await asyncio.wait_for(worker._run_jobs(), 1)
        assert calls == 2
        worker.registry.claim = original
    run(exercise_setup(scenario))


def test_database_outage_returns_safe_retryable_api_error(configured, monkeypatch):
    async def scenario(api, worker, requests):
        async def offline(*args, **kwargs):
            raise RedisConnectionError('redis://user:secret@internal-host')
        monkeypatch.setattr(worker.registry, 'list_jobs', offline)
        response = await api.get('/api/v1/agents/executions', headers=auth())
        assert response.status_code == 503 and response.headers['retry-after'] == '3'
        assert 'secret' not in response.text and 'internal-host' not in response.text
    run(exercise_setup(scenario))


def test_model_reservation_budget_counts_transport_attempts_without_claiming_usage():
    budget = ModelBudget(2, 8192)
    token = current_budget.set(budget)
    try:
        reserve_model_call(4096); reserve_model_call(4096)
        with pytest.raises(ModelBudgetExceeded):
            reserve_model_call(1)
        assert budget.calls == 2 and budget.reserved_output_tokens == 8192
    finally:
        current_budget.reset(token)


def test_legacy_terminal_report_upgrades_without_destructive_migration(configured):
    async def scenario(api, worker, requests):
        # Previous schema had no scope, parent link or configuration version.
        registry = worker.registry
        job = str(uuid.uuid4()); session = str(uuid.uuid4())
        principal = registry.principal('jwt::alice')
        await registry.redis.hset(registry._key(job), mapping={
            'execution_id': job, 'session_id': session, 'principal': principal, 'query': 'old AAPL research',
            'state': 'FAILED', 'created_at': '1', 'updated_at': '1', 'model_version': 'gpt-4o-mini',
            'failure_code': 'OLD_PROVIDER_FAILURE'})
        child, created = await registry.retry(job, principal, 'legacy-retry-token', 'gpt-4o-mini')
        assert created
        record = await registry.get(child, principal)
        assert record['query'] == 'old AAPL research' and record['request_options'] == {}
        assert (await registry.get(job, principal))['state'] == 'FAILED'
    run(exercise_setup(scenario))


def test_configurable_retention_preserves_default_and_bounds_invalid_values(configured, monkeypatch):
    from dexter_py.agent.execution_registry import ExecutionRegistry
    async def scenario(api, worker, requests):
        monkeypatch.setenv('RESEARCH_RETENTION_SECONDS', '3600')
        registry = ExecutionRegistry(worker.registry.redis, prefix=worker.registry.prefix)
        job, _ = await registry.admit(principal='owner', session_id=str(uuid.uuid4()), query='AAPL',
                                     idempotency_key='retention-fixture', model='gpt-4o-mini')
        assert await registry.cancel(job, 'owner') == 'CANCELLED'
        assert 3500 < await registry.redis.ttl(registry._key(job)) <= 3600
        monkeypatch.setenv('RESEARCH_RETENTION_SECONDS', '1')
        with pytest.raises(ValueError):
            ExecutionRegistry(worker.registry.redis, prefix=worker.registry.prefix)
    run(exercise_setup(scenario))


def test_sparse_prices_do_not_claim_daily_volatility_or_sharpe():
    import httpx
    from dexter_py.tools.financial_datasets import FinancialDatasetsClient
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'prices': [
            {'ticker': 'AAPL', 'currency': 'USD', 'date': day, 'close': close}
            for day, close in [('2025-01-01', 100), ('2025-02-01', 110), ('2025-03-01', 99)]
        ]}))) as http:
            provider = FinancialDatasetsClient('fixture', http_client=http)
            result = await provider.risk_metrics('AAPL', '2025-01-01', '2025-03-01')
            assert 'annualized_volatility' not in result['data'] and 'sharpe_ratio' not in result['data']
            assert result['completeness'] == 'partial'
            assert result['data']['total_return'] == pytest.approx(-.01)
    run(scenario())


def test_history_cursor_handles_equal_timestamps_and_expired_records(configured):
    async def scenario(api, worker, requests):
        registry = worker.registry; principal = registry.principal('jwt::alice')
        index = registry.prefix+'jobs:'+principal
        ids = [str(uuid.uuid4()) for _ in range(4)]
        for job in ids:
            await registry.redis.hset(registry._key(job), mapping={
                'execution_id': job, 'session_id': str(uuid.uuid4()), 'principal': principal,
                'query': 'history fixture', 'state': 'FAILED', 'created_at': '10', 'updated_at': '10', 'model_version': 'gpt-4o-mini'})
            await registry.redis.zadd(index, {job: 10})
        await registry.redis.delete(registry._key(max(ids)))
        first = (await api.get('/api/v1/agents/executions?limit=2', headers=auth())).json()
        assert len(first['executions']) == 2 and first['next_cursor']
        second = (await api.get('/api/v1/agents/executions?limit=2&cursor='+first['next_cursor'], headers=auth())).json()
        assert len(second['executions']) == 1 and second['next_cursor'] is None
        assert len({row['execution_id'] for row in first['executions']+second['executions']}) == 3
    run(exercise_setup(scenario))


def test_durable_research_with_ollama_native_http_has_same_validated_report(configured, monkeypatch):
    import httpx
    from dexter_py.model import llm
    from dexter_py.model.ollama_client import OllamaChatClient
    from dexter_py.agent.financial_evidence import ProposedClaims
    async def scenario(api, worker, requests):
        model_calls = []
        def reply(request):
            body = json.loads(request.content); model_calls.append(body)
            assert body['options']['num_predict'] == 4096
            if body['stream']:
                return httpx.Response(200, text=json.dumps({'message': {'content': 'Untrusted draft'}, 'done': False})+'\n'+json.dumps({'message': {'content': ''}, 'done': True, 'done_reason': 'stop'})+'\n')
            schema = json.loads(body['messages'][-1]['content'].rsplit('```json\n', 1)[-1].split('\n```')[0])
            if schema['title'] == 'Understanding':
                result = Understanding(intent='AAPL')
            elif schema['title'] == 'Plan':
                from dexter_py.agent.schemas import PlanTask, ToolCall
                result = Plan(summary='Market research', tasks=[PlanTask(id='market', description='AAPL', tool_calls=[ToolCall(tool='get_price_snapshot', parameters={'ticker': 'AAPL'})])])
            elif schema['title'] == 'ReflectionOutput':
                result = ReflectionOutput(is_complete=True, confidence=.9, reasoning='Provider record available')
            else:
                result = ProposedClaims()
            return httpx.Response(200, json={'message': {'content': result.model_dump_json()}, 'done': True, 'done_reason': 'stop'})
        client = OllamaChatClient('fixture-model', 'http://localhost:11434', http_client=httpx.AsyncClient(transport=httpx.MockTransport(reply)))
        async def get_client(model):
            return client
        monkeypatch.setattr(llm, 'get_llm_client', get_client)
        agent = Orchestrator('ollama-fixture-model', max_iterations=1, tool_executor=worker.orchestrator.execute_phase.tool_executor)
        main.app.state.orchestrator = agent; worker.orchestrator = agent
        try:
            response = await api.post('/api/v1/agents/executions', json={'query': 'AAPL'}, headers=auth(**{'Idempotency-Key': 'ollama-real-transport'}))
            job = response.json()['execution_id']
            assert await worker.process(*(await worker.registry.claim(worker.worker_id, 5))) == 'SUCCEEDED'
            report = (await api.get(f'/api/v1/agents/executions/{job}/result', headers=auth())).json()
            assert 'AAPL price: 100' in report['answer'] and report['evidence']
            assert len(model_calls) == report['model_metadata']['model_calls_reserved'] == 5
            assert report['model_metadata']['output_tokens_reserved'] == 5*4096
            assert 'Untrusted draft' not in report['answer']
        finally:
            await client.aclose()
    run(exercise_setup(scenario))


def test_transient_heartbeat_failure_is_not_mislabeled_as_user_cancellation(configured, monkeypatch):
    async def scenario(api, worker, requests):
        response = await api.post('/api/v1/agents/executions', json={'query': 'AAPL'}, headers=auth(**{'Idempotency-Key': 'heartbeat-outage-race'}))
        job = response.json()['execution_id']
        claim = await worker.registry.claim(worker.worker_id, 5)
        async def unavailable(*args, **kwargs):
            raise RedisConnectionError('temporary heartbeat failure')
        monkeypatch.setattr(worker.registry, 'heartbeat', unavailable)
        assert await asyncio.wait_for(worker.process(*claim), 3) == 'RECOVERABLE'
        # Redis is already back here and would have accepted an erroneous finish.
        principal = worker.registry.principal('jwt::alice')
        record = await worker.registry.get(job, principal)
        assert record['state'] == 'RUNNING' and 'result' not in record
        await worker.registry.redis.hset(worker.registry._key(job), 'lease_expires_at', '0')
        await worker.registry.redis.delete(worker.registry.session_owner_prefix+principal+':'+record['session_id'])
        assert await worker.registry.claim('replacement', 5) == (job, claim[1]+1)
    run(exercise_setup(scenario, 'slow'))
