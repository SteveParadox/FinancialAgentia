import asyncio
import os
import uuid

import pytest
from dexter_py.agent.execution_registry import ExecutionRegistry, ExecutionCapacity
from redis.asyncio import Redis


@pytest.fixture
def url():
    value = os.getenv('TEST_REDIS_URL')
    if not value:
        pytest.skip('Real Redis required for recovery invariants')
    return value


async def scenario(url, callback):
    registry = ExecutionRegistry(Redis.from_url(url, decode_responses=True), prefix='recovery-' + uuid.uuid4().hex + ':')
    try:
        await callback(registry)
    finally:
        keys = [key async for key in registry.redis.scan_iter(match=registry.prefix+'*')]
        if keys:
            await registry.redis.delete(*keys)
        await registry.close()


async def admit(r, principal='u', token=None, **kwargs):
    return (await r.admit(principal=principal, session_id=str(uuid.uuid4()), query='Research AAPL',
                          idempotency_key=token or uuid.uuid4().hex, model='test', **kwargs))[0]


def test_live_cancelled_lease_remains_recoverable_and_cancellation_wins_deadline(url):
    async def check(r):
        job = await admit(r)
        await r.claim('dead-worker')
        assert await r.cancel(job, 'u') == 'CANCEL_REQUESTED'
        assert await r.claim('other-worker') is None
        assert await r.redis.zscore(r.queue, job) is not None
        await r.redis.hset(r._key(job), mapping={'lease_expires_at': '0', 'deadline_at': '0'})
        assert await r.claim('recovery-worker') is None
        assert (await r.get(job, 'u'))['state'] == 'CANCELLED'
    asyncio.run(scenario(url, check))


def test_expired_owner_cannot_publish_and_retries_are_bounded(url):
    async def check(r):
        job = await admit(r, max_attempts=2)
        first = await r.claim('worker-1')
        await r.redis.hset(r._key(job), 'lease_expires_at', '0')
        await r.redis.delete(r.session_owner_prefix+'u:'+(await r.get(job, 'u'))['session_id'])
        second = await r.claim('worker-2')
        assert second[1] == first[1]+1
        assert await r.checkpoint(job, 'worker-1', first[1], {'stale': True}) == 'LOST'
        assert await r.finish(job, 'worker-1', first[1], 'SUCCEEDED') == 'LOST'
        await r.redis.hset(r._key(job), 'lease_expires_at', '0')
        await r.redis.delete(r.session_owner_prefix+'u:'+(await r.get(job, 'u'))['session_id'])
        assert await r.claim('worker-3') is None
        record = await r.get(job, 'u')
        assert record['state'] == 'FAILED' and record['failure_code'] == 'ATTEMPTS_EXHAUSTED'
    asyncio.run(scenario(url, check))


def test_atomic_capacity_limit(url):
    async def check(r):
        results = await asyncio.gather(*(admit(r, token='capacity-key-'+str(i), max_active=2)
                                         for i in range(6)), return_exceptions=True)
        assert sum(isinstance(item, str) for item in results) == 2
        assert sum(isinstance(item, ExecutionCapacity) for item in results) == 4
        accepted = next(item for item in results if isinstance(item, str))
        await r.cancel(accepted, 'u')
        assert await admit(r, max_active=2)
    asyncio.run(scenario(url, check))


def test_more_than_256_jobs_can_be_claimed_without_starvation(url):
    async def check(r):
        for i in range(260):
            await admit(r, principal='owner-'+str(i))
        claims = [await r.claim('worker-'+str(i)) for i in range(260)]
        assert all(claims)
        assert len({claim[0] for claim in claims}) == 260
    asyncio.run(scenario(url, check))


def test_legacy_job_index_upgrade_is_idempotent_and_preserves_data(url):
    from scripts.migrate_execution_index import migrate
    async def check(r):
        job = await admit(r)
        await r.redis.delete(r.prefix+'jobs:u')
        original = await r.redis.hgetall(r._key(job))
        assert await migrate(r) == 1
        assert await r.list_jobs('u') == []
        assert await migrate(r, apply=True) == 1
        assert await migrate(r, apply=True) == 1
        assert (await r.list_jobs('u'))[0]['execution_id'] == job
        assert await r.redis.hgetall(r._key(job)) == original
    asyncio.run(scenario(url, check))


def test_real_process_kill_allows_lease_reclaim(url):
    import subprocess
    import sys
    async def check(r):
        job = await admit(r)
        code = '''import asyncio,sys,time
from redis.asyncio import Redis
from dexter_py.agent.execution_registry import ExecutionRegistry
async def claim():
 r=ExecutionRegistry(Redis.from_url(sys.argv[1],decode_responses=True),prefix=sys.argv[2])
 print(await r.claim('crash-worker',1),flush=True)
 await r.close()
asyncio.run(claim())
time.sleep(60)
'''
        process = subprocess.Popen([sys.executable, '-c', code, url, r.prefix], stdout=subprocess.PIPE, text=True)
        try:
            line = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 5)
            assert job in line
            process.kill()
            await asyncio.to_thread(process.wait, 5)
            await asyncio.sleep(1.1)
            recovered = await r.claim('recovery')
            assert recovered == (job, 2)
            assert await r.finish(job, 'crash-worker', 1, 'SUCCEEDED') == 'LOST'
            assert await r.finish(job, 'recovery', 2, 'FAILED') == 'FAILED'
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
    asyncio.run(scenario(url, check))


def test_new_submission_rate_limit_does_not_break_existing_key_retries(url):
    async def check(r):
        session = str(uuid.uuid4())
        first = await r.admit(principal='u', session_id=session, query='AAPL',
                              idempotency_key='retry-existing-key', model='test')
        for _ in range(9):
            job = await admit(r)
            await r.cancel(job, 'u')
        with pytest.raises(ExecutionCapacity):
            await admit(r)
        retried = await r.admit(principal='u', session_id=session, query='AAPL',
                                idempotency_key='retry-existing-key', model='test')
        assert retried == (first[0], False)
    asyncio.run(scenario(url, check))
