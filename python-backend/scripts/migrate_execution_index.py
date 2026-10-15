"""Add owner history indexes for existing Redis jobs. Dry run unless --apply.

No job contents, TTLs, or execution ownership are changed. Safe to rerun.
Rollback: remove only the added jobs:<principal> sorted sets, retaining exec hashes.
"""
import asyncio
import sys
from dexter_py.agent.execution_registry import ExecutionRegistry


async def migrate(registry, apply=False):
    found = 0
    async for key in registry.redis.scan_iter(match=registry.execution_prefix+'*', count=100):
        record = await registry.redis.hgetall(key)
        if not all(record.get(field) for field in ('principal', 'execution_id', 'created_at')):
            continue
        found += 1
        if apply:
            index = registry.prefix+'jobs:'+record['principal']
            async with registry.redis.pipeline(transaction=True) as pipeline:
                pipeline.zadd(index, {record['execution_id']: float(record['created_at'])})
                pipeline.expire(index, 90000)
                await pipeline.execute()
    return found


async def main():
    registry = ExecutionRegistry.from_env()
    try:
        count = await migrate(registry, '--apply' in sys.argv)
        print(f'{count} existing jobs inspected; applied={"--apply" in sys.argv}')
    finally:
        await registry.close()


if __name__ == '__main__':
    asyncio.run(main())
