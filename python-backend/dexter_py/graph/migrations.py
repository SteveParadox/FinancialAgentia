"""Explicit deployment command: python -m dexter_py.graph.migrations.

DDL is committed separately from the held migration mutex because Neo4j forbids
mixing schema/data updates in one transaction. On crash the mutex releases;
IF NOT EXISTS makes replay safe before recording checksums. No automatic drops.
"""

from __future__ import annotations
import asyncio
from dataclasses import dataclass
import hashlib
from neo4j import Query
from .config import GraphConfig
from .repository import GraphIntegrityError, GraphRepository


@dataclass(frozen=True)
class Migration:
    version: int
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        return hashlib.sha256("\n".join(self.statements).encode()).hexdigest()


MIGRATIONS = (
    Migration(
        1,
        (
            "CREATE CONSTRAINT kg_entity IF NOT EXISTS FOR (n:Entity) REQUIRE (n.scope,n.id) IS UNIQUE",
            "CREATE CONSTRAINT kg_identifier IF NOT EXISTS FOR (n:Identifier) REQUIRE (n.scope,n.key) IS UNIQUE",
            "CREATE CONSTRAINT kg_source IF NOT EXISTS FOR (n:Source) REQUIRE (n.scope,n.id) IS UNIQUE",
            "CREATE CONSTRAINT kg_assertion_fingerprint IF NOT EXISTS FOR (n:Assertion) REQUIRE (n.scope,n.fingerprint) IS UNIQUE",
            "CREATE CONSTRAINT kg_assertion IF NOT EXISTS FOR (n:Assertion) REQUIRE (n.scope,n.id) IS UNIQUE",
            "CREATE CONSTRAINT kg_revision IF NOT EXISTS FOR (n:AssertionRevision) REQUIRE (n.scope,n.assertion_id,n.revision) IS UNIQUE",
            "CREATE CONSTRAINT kg_entity_revision IF NOT EXISTS FOR (n:EntityRevision) REQUIRE (n.scope,n.entity_id,n.revision) IS UNIQUE",
            "CREATE INDEX kg_assertion_time IF NOT EXISTS FOR (n:Assertion) ON (n.scope,n.ingested_at)",
            "CREATE INDEX kg_revision_time IF NOT EXISTS FOR (n:AssertionRevision) ON (n.scope,n.recorded_at)",
        ),
    ),
)


async def migrate(
    repo: GraphRepository, migrations: tuple[Migration, ...] = MIGRATIONS
) -> None:
    if [m.version for m in migrations] != sorted({m.version for m in migrations}):
        raise GraphIntegrityError("Migration versions must be distinct and ordered")
    driver = repo._driver
    async with driver.session(database=repo.config.database) as bootstrap:
        await (
            await bootstrap.run(
                Query(
                    "CREATE CONSTRAINT kg_migration_lock IF NOT EXISTS FOR (m:GraphMigrationLock) REQUIRE m.id IS UNIQUE",
                    timeout=60,
                )
            )
        ).consume()
    async with driver.session(database=repo.config.database) as lock_session:
        async with await lock_session.begin_transaction(timeout=120) as lock:
            # The write lock persists until the migration metadata commits.
            await (
                await lock.run(
                    'MERGE (m:GraphMigrationLock {id:"schema"}) SET m.lock=coalesce(m.lock,0)+1'
                )
            ).consume()
            result = await lock.run(
                "MATCH (m:GraphMigration) RETURN m.version AS version,m.checksum AS checksum ORDER BY m.version"
            )
            applied = {record["version"]: record["checksum"] async for record in result}
            expected = {m.version: m.checksum for m in migrations}
            if any(
                version not in expected or expected[version] != checksum
                for version, checksum in applied.items()
            ):
                raise GraphIntegrityError(
                    "Migration history differs from checked-in checksums"
                )
            if list(applied) != list(expected)[: len(applied)]:
                raise GraphIntegrityError(
                    "Applied migrations must form a contiguous prefix"
                )
            for migration in migrations:
                if migration.version in applied:
                    continue
                async with driver.session(database=repo.config.database) as schema:
                    for statement in migration.statements:
                        # Only checked-in server-owned DDL is accepted by this CLI.
                        await (await schema.run(Query(statement, timeout=60))).consume()
                await (
                    await lock.run(
                        "CREATE (:GraphMigration {version:$version,checksum:$checksum,applied_at:datetime()})",
                        version=migration.version,
                        checksum=migration.checksum,
                    )
                ).consume()
            await lock.commit()


async def main() -> None:
    repo = await GraphRepository.connect(GraphConfig.from_env())
    try:
        await migrate(repo)
        print("Graph migrations applied and checksums verified")
    except Exception:
        raise SystemExit(
            "Graph migration failed; check connectivity, schema privileges and migration history"
        ) from None
    finally:
        await repo.close()


if __name__ == "__main__":
    asyncio.run(main())
