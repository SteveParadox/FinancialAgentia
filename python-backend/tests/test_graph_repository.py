"""Real disposable Neo4j tests. Never run on a production database.

Set GRAPH_TEST_DISPOSABLE=yes and TEST_NEO4J_URI. Each case deletes only its
random scope; schema tests add nondestructive constraints to that disposable DB.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import os
from uuid import uuid4

import pytest
from dexter_py.graph.config import GraphConfig
from dexter_py.graph.migrations import MIGRATIONS, Migration, migrate
from dexter_py.graph.models import (
    GraphAssertion,
    GraphEntity,
    GraphEntityIdentifier,
    GraphEvidenceReference,
    GraphScope,
    GraphRelationshipPredicate,
    GraphSource,
    VerificationStatus,
    now,
)
from dexter_py.graph.repository import (
    GraphConflict,
    GraphIntegrityError,
    GraphRepository,
    GraphUnavailable,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_NEO4J_URI") or os.getenv("GRAPH_TEST_DISPOSABLE") != "yes",
    reason="Explicit disposable Neo4j test runtime required",
)


@asynccontextmanager
async def database():
    config = GraphConfig(
        enabled=True,
        uri=os.environ["TEST_NEO4J_URI"],
        password=os.getenv("TEST_NEO4J_PASSWORD", "disposable-fixture"),
        username=os.getenv("TEST_NEO4J_USERNAME", "neo4j"),
        timeout=3,
    )
    repo = await GraphRepository.connect(config)
    scope = GraphScope(kind="principal", identity="test-" + uuid4().hex)
    try:
        await migrate(repo)
        await repo.verify_schema()
        yield repo, scope
    finally:
        async with repo._driver.session(database=config.database) as session:
            await (
                await session.run(
                    "MATCH (n {scope:$scope}) DETACH DELETE n", scope=scope.key
                )
            ).consume()
        await repo.close()


def make_entity(scope, **changes):
    return GraphEntity(
        scope=scope,
        entity_type="Company",
        canonical_name="Synthetic supplier",
        **changes,
    )


async def populate(repo, scope):
    a, b = make_entity(scope), make_entity(scope)
    await repo.upsert_entity(scope, a)
    await repo.upsert_entity(scope, b)
    source = GraphSource(
        scope=scope,
        provider="Synthetic fixture",
        document_id="fixture:" + uuid4().hex,
        document_type="test filing",
        license="test only",
        verification_status="verified",
    )
    await repo.save_source(scope, source)
    assertion = GraphAssertion(
        scope=scope,
        subject_id=a.id,
        object_id=b.id,
        predicate="SUPPLIES",
        observed_at=now(),
        valid_from=now() - timedelta(days=10),
        valid_to=now() + timedelta(days=10),
        provenance="synthetic test",
        evidence=(GraphEvidenceReference(source_id=source.id, location="p1"),),
    )
    return a, b, source, assertion


def test_connection_entity_source_and_isolation():
    async def case():
        async with database() as (repo, scope):
            a, b, source, _ = await populate(repo, scope)
            assert await repo.get_entity(scope, a.id) == a
            assert await repo.get_source(scope, source.id) == source
            other = GraphScope(kind="principal", identity="other")
            assert await repo.get_entity(other, a.id) is None
            assert await repo.get_source(other, source.id) is None
            assert await repo.assertions_for(other, a.id) == []
            with pytest.raises(GraphIntegrityError):
                await repo.upsert_entity(other, b)

    asyncio.run(case())


def test_identifier_collision_rollback_and_resolution():
    async def case():
        async with database() as (repo, scope):
            cik = GraphEntityIdentifier(
                namespace="SEC_CIK", value="123", authority="SEC"
            )
            a, b = (
                make_entity(scope, identifiers=(cik,)),
                make_entity(scope, identifiers=(cik,)),
            )
            await repo.upsert_entity(scope, a)
            assert await repo.resolve_identifier(scope, cik) == a
            with pytest.raises(GraphConflict):
                await repo.upsert_entity(scope, b)
            assert await repo.get_entity(scope, b.id) is None

    asyncio.run(case())


def test_concurrent_idempotent_entities():
    async def case():
        async with database() as (repo, scope):
            a = make_entity(scope)
            result = await asyncio.gather(
                *(repo.upsert_entity(scope, a) for _ in range(8))
            )
            assert all(row == a for row in result)
            async with repo._driver.session() as session:
                row = await (
                    await session.run(
                        "MATCH (n:Entity {scope:$scope}) RETURN count(n) AS count",
                        scope=scope.key,
                    )
                ).single()
                assert row["count"] == 1

    asyncio.run(case())


def test_entity_metadata_compare_and_swap_preserves_identifier_history():
    async def case():
        async with database() as (repo, scope):
            a = make_entity(
                scope,
                identifiers=(
                    GraphEntityIdentifier(
                        namespace="SEC_CIK", value="123", authority="SEC"
                    ),
                ),
            )
            await repo.upsert_entity(scope, a)
            updated = GraphEntity.model_validate(
                {
                    **a.model_dump(),
                    "canonical_name": "Synthetic legal rename",
                    "updated_at": now(),
                }
            )
            with pytest.raises(GraphConflict):
                await repo.upsert_entity(scope, updated)
            assert (
                await repo.upsert_entity(scope, updated, expected_revision=1) == updated
            )
            removed = GraphEntity.model_validate(
                {**updated.model_dump(), "identifiers": (), "updated_at": now()}
            )
            with pytest.raises(GraphConflict):
                await repo.upsert_entity(scope, removed, expected_revision=2)

    asyncio.run(case())


def test_assertion_concurrency_provenance_and_immutability():
    async def case():
        async with database() as (repo, scope):
            a, _, source, assertion = await populate(repo, scope)
            result = await asyncio.gather(
                *(repo.save_assertion(scope, assertion) for _ in range(6))
            )
            assert all(row == result[0] for row in result)
            assert len(await repo.assertions_for(scope, a.id)) == 1
            changed = assertion.model_copy(update={"provenance": "different evidence"})
            with pytest.raises(GraphConflict):
                await repo.save_assertion(scope, changed)
            with pytest.raises(GraphConflict):
                await repo.save_source(
                    scope, source.model_copy(update={"provider": "changed"})
                )
            async with repo._driver.session() as session:
                record = await (
                    await session.run(
                        "MATCH (a:Assertion {scope:$scope})-[:SUPPORTED_BY]->(s:Source {scope:$scope}) RETURN count(s) AS count",
                        scope=scope.key,
                    )
                ).single()
                assert record["count"] == 1

    asyncio.run(case())


@pytest.mark.parametrize(
    "failure",
    [
        "missing_source",
        "wrong_direction",
        "missing_endpoint",
        "cross_scope_source",
        "direct_verified",
        "missing_prior",
    ],
)
def test_atomic_assertion_rejection(failure):
    async def case():
        async with database() as (repo, scope):
            a, _, _, assertion = await populate(repo, scope)
            if failure == "cross_scope_source":
                hidden_scope = GraphScope(
                    kind="workspace", identity="hidden-" + uuid4().hex
                )
                hidden_source = GraphSource(
                    scope=hidden_scope,
                    provider="fixture",
                    document_id="hidden",
                    document_type="test",
                    license="test",
                )
                await repo.save_source(hidden_scope, hidden_source)
                # Remove only this synthetic source after testing the scoped lookup.
                try:
                    with pytest.raises(GraphIntegrityError):
                        await repo.save_assertion(
                            scope,
                            assertion.model_copy(
                                update={
                                    "evidence": (
                                        GraphEvidenceReference(
                                            source_id=hidden_source.id, location="p1"
                                        ),
                                    )
                                }
                            ),
                        )
                finally:
                    async with repo._driver.session() as session:
                        await (
                            await session.run(
                                "MATCH (n {scope:$scope}) DETACH DELETE n",
                                scope=hidden_scope.key,
                            )
                        ).consume()
                return
            updates = {
                "missing_source": {
                    "evidence": (
                        GraphEvidenceReference(source_id=uuid4(), location="p2"),
                    )
                },
                "wrong_direction": {
                    "predicate": GraphRelationshipPredicate.EXECUTIVE_OF
                },
                "missing_endpoint": {"object_id": uuid4()},
                "cross_scope_source": {
                    "evidence": (
                        GraphEvidenceReference(source_id=uuid4(), location="p3"),
                    )
                },
                "direct_verified": {"verification_status": VerificationStatus.VERIFIED},
                "missing_prior": {"supersedes": uuid4()},
            }[failure]
            with pytest.raises((GraphIntegrityError, ValueError)):
                await repo.save_assertion(scope, assertion.model_copy(update=updates))
            assert await repo.assertions_for(scope, a.id) == []

    asyncio.run(case())


def test_lifecycle_bitemporal_history_and_competing_assertions():
    async def case():
        async with database() as (repo, scope):
            a, _, _, assertion = await populate(repo, scope)
            before = now()
            saved = await repo.save_assertion(scope, assertion)
            proposed_time = now()
            assert await repo.assertions_for(scope, a.id, known_at=before) == []
            assert (
                await repo.transition(
                    scope,
                    assertion.id,
                    expected_revision=1,
                    status=VerificationStatus.VERIFIED,
                    actor="reviewer",
                    reason="source checked",
                )
                == 2
            )
            verified_time = now()
            with pytest.raises(GraphConflict):
                await repo.transition(
                    scope,
                    assertion.id,
                    expected_revision=1,
                    status=VerificationStatus.RETRACTED,
                    actor="reviewer",
                    reason="stale",
                )
            await repo.transition(
                scope,
                assertion.id,
                expected_revision=2,
                status=VerificationStatus.CONTRADICTED,
                actor="reviewer",
                reason="competing evidence",
            )
            competing = GraphAssertion.model_validate(
                {**assertion.model_dump(), "id": uuid4(), "supersedes": assertion.id}
            )
            await repo.save_assertion(scope, competing)
            rows = await repo.assertions_for(scope, a.id)
            assert {row.verification_status for row in rows} == {
                VerificationStatus.CONTRADICTED,
                VerificationStatus.PROPOSED,
            }
            assert (await repo.assertions_for(scope, a.id, known_at=proposed_time))[
                0
            ].verification_status == VerificationStatus.PROPOSED
            assert (await repo.assertions_for(scope, a.id, known_at=verified_time))[
                0
            ].verification_status == VerificationStatus.VERIFIED
            assert len(await repo.assertions_for(scope, a.id, valid_at=now())) == 2
            assert (
                await repo.assertions_for(
                    scope, a.id, valid_at=now() - timedelta(days=20)
                )
                == []
            )
            assert saved.valid_from == assertion.valid_from

    asyncio.run(case())


def test_verified_requires_verified_source_and_unknown_validity_not_inferred():
    async def case():
        async with database() as (repo, scope):
            a, _, source, assertion = await populate(repo, scope)
            unverified = source.model_copy(
                update={
                    "id": uuid4(),
                    "verification_status": VerificationStatus.PROPOSED,
                }
            )
            await repo.save_source(scope, unverified)
            assertion = assertion.model_copy(
                update={
                    "valid_from": None,
                    "valid_to": None,
                    "evidence": (
                        GraphEvidenceReference(source_id=unverified.id, location="p1"),
                    ),
                }
            )
            await repo.save_assertion(scope, assertion)
            with pytest.raises(GraphIntegrityError):
                await repo.transition(
                    scope,
                    assertion.id,
                    expected_revision=1,
                    status=VerificationStatus.VERIFIED,
                    actor="reviewer",
                    reason="confidence alone",
                )
            assert len(await repo.assertions_for(scope, a.id)) == 1
            assert await repo.assertions_for(scope, a.id, valid_at=now()) == []

    asyncio.run(case())


def test_concurrent_lifecycle_compare_and_swap():
    async def case():
        async with database() as (repo, scope):
            _, _, _, assertion = await populate(repo, scope)
            await repo.save_assertion(scope, assertion)
            results = await asyncio.gather(
                *(
                    repo.transition(
                        scope,
                        assertion.id,
                        expected_revision=1,
                        status=VerificationStatus.VERIFIED,
                        actor="reviewer",
                        reason="checked",
                    )
                    for _ in range(4)
                ),
                return_exceptions=True,
            )
            assert results.count(2) == 1
            assert sum(isinstance(row, GraphConflict) for row in results) == 3

    asyncio.run(case())


def test_database_constraint_independent_of_repository():
    async def case():
        from neo4j.exceptions import ConstraintError

        async with database() as (repo, scope):
            a = make_entity(scope)
            await repo.upsert_entity(scope, a)
            async with repo._driver.session() as session:
                with pytest.raises(ConstraintError):
                    await (
                        await session.run(
                            "CREATE (:Entity {scope:$scope,id:$id})",
                            scope=scope.key,
                            id=str(a.id),
                        )
                    ).consume()

    asyncio.run(case())


def test_migration_replay_race_checksum_and_ordering():
    async def case():
        async with database() as (repo, _):
            await asyncio.gather(*(migrate(repo) for _ in range(3)))
            await migrate(repo)
            with pytest.raises(GraphIntegrityError):
                await migrate(repo, (Migration(1, ("RETURN 1",)),))
            with pytest.raises(GraphIntegrityError):
                await migrate(repo, (Migration(2, ()), MIGRATIONS[0]))

    asyncio.run(case())


def test_query_limits_and_injection_are_data():
    async def case():
        async with database() as (repo, scope):
            a = make_entity(scope)
            a = a.model_copy(
                update={"canonical_name": "x' MATCH (n) DETACH DELETE n //"}
            )
            await repo.upsert_entity(scope, a)
            assert (
                await repo.get_entity(scope, a.id)
            ).canonical_name == a.canonical_name
            with pytest.raises(GraphIntegrityError):
                await repo.assertions_for(scope, a.id, limit=1000)

    asyncio.run(case())


def test_connection_failure_is_explicit_and_redacted():
    async def case():
        config = GraphConfig(
            enabled=True, uri="bolt://127.0.0.1:1", password="never-log-this", timeout=1
        )
        with pytest.raises(GraphUnavailable) as exc:
            await GraphRepository.connect(config)
        assert "never-log-this" not in str(exc.value)

    asyncio.run(case())


def test_duplicate_content_cannot_evade_identity_with_new_uuid():
    async def case():
        async with database() as (repo, scope):
            a, _, _, assertion = await populate(repo, scope)
            await repo.save_assertion(scope, assertion)
            duplicate = assertion.model_copy(update={"id": uuid4()})
            with pytest.raises(GraphConflict):
                await repo.save_assertion(scope, duplicate)
            assert len(await repo.assertions_for(scope, a.id)) == 1

    asyncio.run(case())


def test_cancellation_rolls_back_incomplete_transaction():
    async def case():
        async with database() as (repo, scope):
            started = asyncio.Event()
            identity = uuid4()

            async def partial(tx):
                await repo._run(
                    tx,
                    "CREATE (:Entity {scope:$scope,id:$id})",
                    scope=scope.key,
                    id=str(identity),
                )
                started.set()
                await asyncio.Event().wait()

            task = asyncio.create_task(repo._transaction(True, partial))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await repo.get_entity(scope, identity) is None

    asyncio.run(case())


def test_schema_failure_does_not_record_partial_migration():
    async def case():
        from neo4j.exceptions import Neo4jError

        async with database() as (repo, _):
            bad = Migration(
                2,
                (
                    "CREATE INDEX kg_replay_probe IF NOT EXISTS FOR (n:MigrationReplayProbe) ON (n.id)",
                    "THIS IS NOT CYPHER",
                ),
            )
            with pytest.raises(Neo4jError):
                await migrate(repo, (*MIGRATIONS, bad))
            # Initial valid schema step may remain, but history never claims v2 succeeded.
            await migrate(repo)
            async with repo._driver.session() as session:
                row = await (
                    await session.run(
                        "MATCH (m:GraphMigration {version:2}) RETURN count(m) AS count"
                    )
                ).single()
                assert row["count"] == 0

    asyncio.run(case())


def test_entity_metadata_is_retrievable_at_historical_knowledge_time():
    async def case():
        from datetime import datetime

        async with database() as (repo, scope):
            a = make_entity(scope)
            before_creation = now()
            await repo.upsert_entity(scope, a)
            before_rename = now()
            renamed = GraphEntity.model_validate(
                {
                    **a.model_dump(),
                    "canonical_name": "Synthetic renamed company",
                    "updated_at": now(),
                }
            )
            await repo.upsert_entity(scope, renamed, expected_revision=1)
            assert await repo.get_entity(scope, a.id, known_at=before_creation) is None
            assert await repo.get_entity(scope, a.id, known_at=before_rename) == a
            assert await repo.get_entity(scope, a.id, known_at=now()) == renamed
            assert await repo.get_entity(scope, a.id) == renamed
            with pytest.raises(GraphIntegrityError):
                await repo.get_entity(
                    scope, a.id, known_at=datetime(2024, 1, 1)
                )

    asyncio.run(case())


def test_evidence_order_cannot_bypass_semantic_deduplication():
    async def case():
        async with database() as (repo, scope):
            a, _, source, assertion = await populate(repo, scope)
            second = GraphSource.model_validate(
                {
                    **source.model_dump(),
                    "id": uuid4(),
                    "document_id": "fixture:another-source",
                }
            )
            await repo.save_source(scope, second)
            refs = (
                GraphEvidenceReference(source_id=source.id, location="page 1"),
                GraphEvidenceReference(source_id=second.id, location="page 2"),
            )
            first = assertion.model_copy(update={"evidence": refs})
            saved = await repo.save_assertion(scope, first)
            reordered = first.model_copy(update={"evidence": tuple(reversed(refs))})
            assert await repo.save_assertion(scope, reordered) == saved
            with pytest.raises(GraphConflict):
                await repo.save_assertion(
                    scope, reordered.model_copy(update={"id": uuid4()})
                )
            assert len(await repo.assertions_for(scope, a.id)) == 1

    asyncio.run(case())
