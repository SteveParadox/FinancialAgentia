"""Trusted internal graph service. Every operation requires an authorized scope.

Only the API dependency may derive scopes from user requests. Never give this
object or its credentials to a model, tool executor or arbitrary query endpoint.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
import hashlib
import asyncio
import time
from typing import LiteralString, TypeVar
from uuid import UUID

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncManagedTransaction, unit_of_work
from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired

from .config import GraphConfig
from .metrics import OPERATIONS, DURATION
from .models import (
    GraphAssertion,
    GraphEntity,
    GraphEntityIdentifier,
    GraphScope,
    GraphSource,
    VerificationStatus,
    now,
    validate_direction,
)

T = TypeVar("T")


class GraphError(RuntimeError):
    """Safe public message; do not serialize driver errors or query parameters."""


class GraphUnavailable(GraphError):
    pass


class GraphConflict(GraphError):
    pass


class GraphIntegrityError(GraphError):
    pass


class GraphRepository:
    def __init__(self, driver: AsyncDriver, config: GraphConfig):
        self._driver = driver
        self.config = config

    @classmethod
    async def connect(cls, config: GraphConfig) -> GraphRepository:
        if not config.enabled:
            raise GraphUnavailable("Graph functionality is disabled")
        driver = AsyncGraphDatabase.driver(
            config.uri,
            auth=(config.username, config.password.get_secret_value()),
            max_connection_pool_size=config.pool_size,
            connection_timeout=config.timeout,
            connection_acquisition_timeout=config.timeout,
            max_transaction_retry_time=config.timeout,
        )
        repo = cls(driver, config)
        try:
            await repo.verify_connectivity()
        except BaseException:
            await driver.close()
            raise
        return repo

    async def verify_schema(self) -> None:
        async def check(tx: AsyncManagedTransaction) -> None:
            rows = await self._run(tx, "SHOW CONSTRAINTS YIELD name RETURN name")
            required = {
                "kg_entity",
                "kg_identifier",
                "kg_source",
                "kg_assertion",
                "kg_assertion_fingerprint",
                "kg_revision",
                "kg_entity_revision",
            }
            if not required <= {str(row["name"]) for row in rows}:
                raise GraphIntegrityError(
                    "Graph schema missing; run explicit graph migrations"
                )

        await self._transaction(False, check)

    async def close(self) -> None:
        await self._driver.close()

    async def verify_connectivity(self) -> None:
        try:
            await self._driver.verify_connectivity()

            async def check(tx: AsyncManagedTransaction) -> None:
                await (await tx.run("RETURN 1 AS ok")).consume()

            await self._transaction(False, check)
        except (Neo4jError, ServiceUnavailable, SessionExpired):
            raise GraphUnavailable("Graph database unavailable") from None

    async def _transaction(
        self, write: bool, operation: Callable[[AsyncManagedTransaction], Awaitable[T]]
    ) -> T:
        started = time.monotonic()
        mode = "write" if write else "read"
        outcome = "failed"
        try:
            async with self._driver.session(database=self.config.database) as session:
                callback = unit_of_work(timeout=self.config.timeout)(operation)
                result = (
                    await session.execute_write(callback)
                    if write
                    else await session.execute_read(callback)
                )
                outcome = "success"
                return result
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except GraphConflict:
            outcome = "conflict"
            raise
        except GraphIntegrityError:
            outcome = "invalid"
            raise
        except (ServiceUnavailable, SessionExpired):
            outcome = "unavailable"
            raise GraphUnavailable("Graph database unavailable") from None
        except Neo4jError as exc:
            if exc.code and "Constraint" in exc.code:
                outcome = "conflict"
                raise GraphConflict("Graph identity or revision conflict") from None
            outcome = "unavailable"
            raise GraphUnavailable("Graph database operation failed") from None
        finally:
            OPERATIONS.labels(mode, outcome).inc()
            DURATION.labels(mode).observe(time.monotonic() - started)

    async def _run(
        self, tx: AsyncManagedTransaction, query: LiteralString, **parameters: object
    ) -> list[dict[str, object]]:
        result = await tx.run(query, parameters)
        return [record.data() async for record in result]

    @staticmethod
    def _scope(
        scope: GraphScope, value: GraphEntity | GraphSource | GraphAssertion
    ) -> None:
        if value.scope != scope:
            raise GraphIntegrityError("Scope mismatch")

    async def upsert_entity(
        self, scope: GraphScope, entity: GraphEntity, *, expected_revision: int = 0
    ) -> GraphEntity:
        """Create/replay or compare-and-swap metadata. Identifier ownership is permanent."""
        self._scope(scope, entity)
        payload = entity.canonical_json()

        async def write(tx: AsyncManagedTransaction) -> GraphEntity:
            rows = await self._run(
                tx,
                """MERGE (e:Entity {scope:$scope,id:$id})
                ON CREATE SET e.payload=$payload,e.revision=0
                SET e.lock=coalesce(e.lock,0)+1
                RETURN e.payload AS payload,e.revision AS revision""",
                scope=scope.key,
                id=str(entity.id),
                payload=payload,
            )
            old = GraphEntity.model_validate_json(str(rows[0]["payload"]))
            revision = int(str(rows[0]["revision"]))
            if revision and rows[0]["payload"] != payload:
                if (
                    revision != expected_revision
                    or old.entity_type != entity.entity_type
                    or old.created_at != entity.created_at
                    or entity.updated_at <= old.updated_at
                ):
                    raise GraphConflict("Entity revision conflict")
                if not {i.key for i in old.identifiers} <= {
                    i.key for i in entity.identifiers
                }:
                    raise GraphConflict("Identifier history cannot be removed")
            elif revision:
                return old
            for identifier in entity.identifiers:
                rows = await self._run(
                    tx,
                    """MERGE (i:Identifier {scope:$scope,key:$key})
                    ON CREATE SET i.entity_id=$id,i.payload=$payload
                    WITH i MATCH (e:Entity {scope:$scope,id:$id})
                    MERGE (e)-[:IDENTIFIED_BY]->(i) RETURN i.entity_id AS owner""",
                    scope=scope.key,
                    key=identifier.key,
                    id=str(entity.id),
                    payload=identifier.canonical_json(),
                )
                if rows[0]["owner"] != str(entity.id):
                    raise GraphConflict("Authoritative identifier already assigned")
            await self._run(
                tx,
                """MATCH (e:Entity {scope:$scope,id:$id})
                SET e.payload=$payload,e.entity_type=$entity_type,e.revision=$revision
                CREATE (v:EntityRevision {scope:$scope,entity_id:$id,revision:$revision,payload:$payload,recorded_at:$at})
                CREATE (e)-[:HAS_ENTITY_REVISION]->(v)""",
                scope=scope.key,
                id=str(entity.id),
                payload=payload,
                entity_type=entity.entity_type.value,
                revision=revision + 1,
                at=now().timestamp(),
            )
            return entity

        return await self._transaction(True, write)

    async def get_entity(self, scope: GraphScope, identity: UUID) -> GraphEntity | None:
        async def read(tx: AsyncManagedTransaction) -> GraphEntity | None:
            rows = await self._run(
                tx,
                "MATCH (e:Entity {scope:$scope,id:$id}) RETURN e.payload AS payload",
                scope=scope.key,
                id=str(identity),
            )
            return (
                GraphEntity.model_validate_json(str(rows[0]["payload"]))
                if rows
                else None
            )

        return await self._transaction(False, read)

    async def resolve_identifier(
        self, scope: GraphScope, identifier: GraphEntityIdentifier
    ) -> GraphEntity | None:
        async def read(tx: AsyncManagedTransaction) -> GraphEntity | None:
            rows = await self._run(
                tx,
                """MATCH (e:Entity {scope:$scope})-[:IDENTIFIED_BY]->(i:Identifier {scope:$scope,key:$key})
                RETURN e.payload AS payload""",
                scope=scope.key,
                key=identifier.key,
            )
            return (
                GraphEntity.model_validate_json(str(rows[0]["payload"]))
                if rows
                else None
            )

        return await self._transaction(False, read)

    async def save_source(self, scope: GraphScope, source: GraphSource) -> GraphSource:
        self._scope(scope, source)

        async def write(tx: AsyncManagedTransaction) -> GraphSource:
            rows = await self._run(
                tx,
                """MERGE (s:Source {scope:$scope,id:$id})
                ON CREATE SET s.payload=$payload,s.status=$status
                RETURN s.payload AS payload""",
                scope=scope.key,
                id=str(source.id),
                payload=source.canonical_json(),
                status=source.verification_status.value,
            )
            if rows[0]["payload"] != source.canonical_json():
                raise GraphConflict(
                    "Sources are immutable; create a new source snapshot"
                )
            return source

        return await self._transaction(True, write)

    async def get_source(self, scope: GraphScope, identity: UUID) -> GraphSource | None:
        async def read(tx: AsyncManagedTransaction) -> GraphSource | None:
            rows = await self._run(
                tx,
                "MATCH (s:Source {scope:$scope,id:$id}) RETURN s.payload AS payload",
                scope=scope.key,
                id=str(identity),
            )
            return (
                GraphSource.model_validate_json(str(rows[0]["payload"]))
                if rows
                else None
            )

        return await self._transaction(False, read)

    async def _evidence(
        self,
        tx: AsyncManagedTransaction,
        scope: GraphScope,
        assertion: GraphAssertion,
        verified: bool,
    ) -> None:
        if verified and not assertion.evidence:
            raise GraphIntegrityError("Verification requires evidence")
        for ref in assertion.evidence:
            rows = await self._run(
                tx,
                "MATCH (s:Source {scope:$scope,id:$id}) RETURN s.status AS status",
                scope=scope.key,
                id=str(ref.source_id),
            )
            if not rows or (
                verified and rows[0]["status"] != VerificationStatus.VERIFIED.value
            ):
                raise GraphIntegrityError(
                    "Missing source or insufficient verified provenance"
                )

    async def save_assertion(
        self, scope: GraphScope, assertion: GraphAssertion
    ) -> GraphAssertion:
        self._scope(scope, assertion)
        if assertion.verification_status != VerificationStatus.PROPOSED:
            raise GraphIntegrityError(
                "New assertions must be proposed; use audited verification lifecycle"
            )
        # Caller supplies a stable assertion ID for retry. Stored transaction time is server-owned.
        semantic = assertion.model_dump(exclude={"ingested_at", "id"}, mode="json")
        import json

        fingerprint = hashlib.sha256(
            json.dumps(semantic, sort_keys=True).encode()
        ).hexdigest()
        stored = GraphAssertion.model_validate(
            {**assertion.model_dump(), "ingested_at": now()}
        )

        async def write(tx: AsyncManagedTransaction) -> GraphAssertion:
            rows = await self._run(
                tx,
                """MATCH (s:Entity {scope:$scope,id:$subject}),(o:Entity {scope:$scope,id:$object})
                RETURN s.payload AS subject,o.payload AS object""",
                scope=scope.key,
                subject=str(stored.subject_id),
                object=str(stored.object_id),
            )
            if not rows:
                raise GraphIntegrityError("Missing assertion endpoints")
            validate_direction(
                GraphEntity.model_validate_json(str(rows[0]["subject"])),
                stored,
                GraphEntity.model_validate_json(str(rows[0]["object"])),
            )
            await self._evidence(tx, scope, stored, False)
            if stored.supersedes:
                prior = await self._run(
                    tx,
                    """MATCH (a:Assertion {scope:$scope,id:$id})
                    RETURN a.subject_id AS subject,a.object_id AS object,a.predicate AS predicate""",
                    scope=scope.key,
                    id=str(stored.supersedes),
                )
                if not prior or prior[0] != {
                    "subject": str(stored.subject_id),
                    "object": str(stored.object_id),
                    "predicate": stored.predicate.value,
                }:
                    raise GraphIntegrityError(
                        "Superseded assertion must describe the same scoped relationship"
                    )
            rows = await self._run(
                tx,
                """MERGE (a:Assertion {scope:$scope,id:$id})
                ON CREATE SET a.payload=$payload,a.fingerprint=$fingerprint,a.ingested_at=$at,
                a.valid_from=$valid_from,a.valid_to=$valid_to,a.subject_id=$subject,a.object_id=$object,
                a.predicate=$predicate,a.revision=1
                RETURN a.payload AS payload,a.fingerprint AS fingerprint""",
                scope=scope.key,
                id=str(stored.id),
                payload=stored.canonical_json(),
                fingerprint=fingerprint,
                at=stored.ingested_at.timestamp(),
                valid_from=stored.valid_from.timestamp() if stored.valid_from else None,
                valid_to=stored.valid_to.timestamp() if stored.valid_to else None,
                subject=str(stored.subject_id),
                object=str(stored.object_id),
                predicate=stored.predicate.value,
            )
            if rows[0]["fingerprint"] != fingerprint:
                raise GraphConflict("Assertion identity is immutable")
            persisted = GraphAssertion.model_validate_json(str(rows[0]["payload"]))
            await self._run(
                tx,
                """MATCH (a:Assertion {scope:$scope,id:$id}),
                (s:Entity {scope:$scope,id:$subject}),(o:Entity {scope:$scope,id:$object})
                MERGE (s)-[:ASSERTION_SUBJECT]->(a) MERGE (a)-[:ASSERTION_OBJECT]->(o)
                MERGE (v:AssertionRevision {scope:$scope,assertion_id:$id,revision:1})
                ON CREATE SET v.status='proposed',v.recorded_at=$at,v.actor='system',v.reason='initial proposal'
                MERGE (a)-[:HAS_REVISION]->(v)""",
                scope=scope.key,
                id=str(stored.id),
                subject=str(stored.subject_id),
                object=str(stored.object_id),
                at=persisted.ingested_at.timestamp(),
            )
            for ref in stored.evidence:
                await self._run(
                    tx,
                    """MATCH (a:Assertion {scope:$scope,id:$id}),(s:Source {scope:$scope,id:$source})
                    MERGE (a)-[:SUPPORTED_BY]->(s)""",
                    scope=scope.key,
                    id=str(stored.id),
                    source=str(ref.source_id),
                )
            return persisted

        return await self._transaction(True, write)

    async def transition(
        self,
        scope: GraphScope,
        identity: UUID,
        *,
        expected_revision: int,
        status: VerificationStatus,
        actor: str,
        reason: str,
    ) -> int:
        """Trusted reviewer service only. Compare-and-swap, append-only status history."""
        if (
            not 1 <= expected_revision
            or not actor.strip()
            or not reason.strip()
            or len(actor) > 128
            or len(reason) > 500
        ):
            raise GraphIntegrityError("Invalid verification audit metadata")
        allowed = {
            VerificationStatus.PROPOSED: {
                VerificationStatus.VERIFIED,
                VerificationStatus.CONTRADICTED,
                VerificationStatus.RETRACTED,
                VerificationStatus.SUPERSEDED,
            },
            VerificationStatus.VERIFIED: {
                VerificationStatus.CONTRADICTED,
                VerificationStatus.RETRACTED,
                VerificationStatus.SUPERSEDED,
            },
        }

        async def write(tx: AsyncManagedTransaction) -> int:
            rows = await self._run(
                tx,
                """MATCH (a:Assertion {scope:$scope,id:$id})
                SET a.lock=coalesce(a.lock,0)+1
                WITH a MATCH (a)-[:HAS_REVISION]->(v:AssertionRevision)
                WHERE v.revision=a.revision
                RETURN a.payload AS payload,a.revision AS revision,v.status AS status,v.recorded_at AS at""",
                scope=scope.key,
                id=str(identity),
            )
            if not rows or rows[0]["revision"] != expected_revision:
                raise GraphConflict("Assertion revision conflict")
            current = VerificationStatus(str(rows[0]["status"]))
            if status not in allowed.get(current, set()):
                raise GraphIntegrityError(
                    "Invalid lifecycle transition; create new assertion for new evidence"
                )
            assertion = GraphAssertion.model_validate_json(str(rows[0]["payload"]))
            await self._evidence(
                tx, scope, assertion, status == VerificationStatus.VERIFIED
            )
            at = max(now().timestamp(), float(str(rows[0]["at"])) + 0.000001)
            await self._run(
                tx,
                """MATCH (a:Assertion {scope:$scope,id:$id}) SET a.revision=$revision
                CREATE (v:AssertionRevision {scope:$scope,assertion_id:$id,revision:$revision,
                status:$status,recorded_at:$at,actor:$actor,reason:$reason})
                CREATE (a)-[:HAS_REVISION]->(v)""",
                scope=scope.key,
                id=str(identity),
                revision=expected_revision + 1,
                status=status.value,
                at=at,
                actor=actor,
                reason=reason,
            )
            return expected_revision + 1

        return await self._transaction(True, write)

    async def assertions_for(
        self,
        scope: GraphScope,
        identity: UUID,
        *,
        known_at: datetime | None = None,
        valid_at: datetime | None = None,
        limit: int = 50,
    ) -> list[GraphAssertion]:
        """Bounded one-hop assertions including competing and nonverified facts.

        Known time selects immutable lifecycle revision. Valid time excludes unknown
        boundaries; null dates never silently become negative/positive infinity.
        """
        if not 1 <= limit <= 100:
            raise GraphIntegrityError("Graph query limit must be 1..100")
        for date in (known_at, valid_at):
            if date and (date.tzinfo is None or date.utcoffset() is None):
                raise GraphIntegrityError(
                    "Historical timestamps must be timezone aware"
                )

        async def read(tx: AsyncManagedTransaction) -> list[GraphAssertion]:
            rows = await self._run(
                tx,
                """MATCH (e:Entity {scope:$scope,id:$id})
                MATCH (e)-[:ASSERTION_SUBJECT|ASSERTION_OBJECT]-(a:Assertion {scope:$scope})
                WHERE a.ingested_at <= $known
                  AND ($valid IS NULL OR (a.valid_from <= $valid AND a.valid_to > $valid))
                CALL { WITH a MATCH (a)-[:HAS_REVISION]->(v:AssertionRevision {scope:$scope})
                    WHERE v.recorded_at <= $known RETURN v ORDER BY v.revision DESC LIMIT 1 }
                RETURN a.payload AS payload,v.status AS status ORDER BY a.ingested_at,a.id LIMIT $limit""",
                scope=scope.key,
                id=str(identity),
                known=(known_at or now()).timestamp(),
                valid=valid_at.timestamp() if valid_at else None,
                limit=limit,
            )
            return [
                GraphAssertion.model_validate(
                    {
                        **GraphAssertion.model_validate_json(
                            str(row["payload"])
                        ).model_dump(),
                        "verification_status": row["status"],
                    }
                )
                for row in rows
            ]

        return await self._transaction(False, read)
