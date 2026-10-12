"""Real Redis multi-owner concurrency invariants. Requires TEST_REDIS_URL."""
import asyncio
import os
import uuid

import pytest

from dexter_py.agent.execution_registry import ExecutionRegistry, ExecutionConflict, TERMINAL


@pytest.fixture
def registry():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL not available; distributed tests were NOT executed")
    yield url


def run(awaitable):
    return asyncio.run(awaitable)


async def new_registry(url: str):
    from redis.asyncio import Redis
    client = Redis.from_url(url, decode_responses=True)
    await client.ping()
    return ExecutionRegistry(client, prefix="test-financial-" + uuid.uuid4().hex + ":")


def test_same_idempotency_key_admits_exactly_once(registry):
    async def check():
        instance = await new_registry(registry)
        try:
            await exercise(instance)
        finally:
            await instance.close()
    async def exercise(registry):
        # Same session ID required for the same logical request.
        session = str(uuid.uuid4())
        tasks = [registry.admit(principal="user-1", session_id=session,
                                query="AAPL price", idempotency_key="shared-key-123",
                                model="test-model") for _ in range(10)]
        result = await asyncio.gather(*tasks)
        assert len({item[0] for item in result}) == 1
        assert sum(item[1] for item in result) == 1
        with pytest.raises(ExecutionConflict):
            await registry.admit(principal="user-1", session_id=session,
                                 query="MSFT price", idempotency_key="shared-key-123",
                                 model="test-model")
    run(check())


def test_two_workers_cannot_claim_same_lease(registry):
    async def check():
        instance = await new_registry(registry)
        try:
            await exercise(instance)
        finally:
            await instance.close()
    async def exercise(registry):
        entry, _ = await registry.admit(
            principal="u", session_id=str(uuid.uuid4()), query="AAPL",
            idempotency_key="fresh-claim-123", model="test")
        claims = await asyncio.gather(registry.claim("worker-a"), registry.claim("worker-b"))
        assert sum(item is not None for item in claims) == 1
        claimed = next(item for item in claims if item)
        assert claimed[0] == entry
        assert await registry.heartbeat(entry, "wrong-owner", claimed[1]) == "LOST"
        assert await registry.finish(entry, "wrong-owner", claimed[1], "SUCCEEDED", result={"answer": "bad"}) == "LOST"
    run(check())


def test_remote_cancel_blocks_success_and_is_idempotent(registry):
    async def check():
        instance = await new_registry(registry)
        try:
            await exercise(instance)
        finally:
            await instance.close()
    async def exercise(registry):
        entry, _ = await registry.admit(
            principal="u", session_id=str(uuid.uuid4()), query="Research AAPL",
            idempotency_key="cancel-key-123", model="test")
        worker = await registry.claim("worker-a")
        assert worker is not None
        assert await registry.cancel(entry, "other") == "NOT_FOUND"
        assert await registry.cancel(entry, "u") == "CANCEL_REQUESTED"
        assert await registry.cancel(entry, "u") == "CANCEL_REQUESTED"
        assert await registry.heartbeat(entry, "worker-a", worker[1]) == "CANCEL"
        assert await registry.finish(entry, "worker-a", worker[1],
                                     "SUCCEEDED", result={"answer": "should not commit"}) == "CANCELLED"
        assert (await registry.get(entry, "u"))["state"] == "CANCELLED"
        assert await registry.history("u", (await registry.get(entry, "u"))["session_id"]) == []
    run(check())


def test_final_commit_once_and_history_once(registry):
    async def check():
        instance = await new_registry(registry)
        try:
            await exercise(instance)
        finally:
            await instance.close()
    async def exercise(registry):
        session = str(uuid.uuid4())
        entry, _ = await registry.admit(
            principal="u", session_id=session, query="Research AAPL",
            idempotency_key="final-key-123", model="test")
        claimed = await registry.claim("worker-a")
        assert claimed is not None
        assert await registry.finish(entry, "worker-a", claimed[1], "SUCCEEDED",
                                     result={"answer": "Verified price"}) == "SUCCEEDED"
        assert await registry.finish(entry, "worker-a", claimed[1], "SUCCEEDED",
                                     result={"answer": "Duplication"}) == "LOST"
        history = await registry.history("u", session)
        assert len(history) == 1
        assert history[0]["answer"] == "Verified price"
    run(check())
