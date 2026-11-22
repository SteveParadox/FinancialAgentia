"""Graph endpoints reuse existing auth, principal hashing and workspace ACLs."""

import asyncio
import os
from uuid import uuid4
import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from app import main
from dexter_py.graph.models import GraphEntity, GraphScope


def test_graph_disabled_authentication_and_lifecycle(monkeypatch):
    monkeypatch.setenv("GRAPH_ENABLED", "false")
    monkeypatch.setenv("BACKEND_API_KEY", "graph-fixture-key")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    with TestClient(main.app) as client:
        assert main.app.state.graph_repository is None
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/graph/ontology").status_code == 401
        assert client.get("/api/v1/graph/entities/" + str(uuid4())).status_code == 401
        assert (
            client.get(
                "/api/v1/graph/entities/" + str(uuid4()),
                headers={"x-api-key": "graph-fixture-key"},
            ).status_code
            == 503
        )


@pytest.mark.skipif(
    not os.getenv("TEST_REDIS_URL")
    or not os.getenv("TEST_NEO4J_URI")
    or os.getenv("GRAPH_TEST_DISPOSABLE") != "yes",
    reason="Disposable Redis and Neo4j required",
)
def test_authenticated_graph_workspace_and_principal_isolation(monkeypatch):
    monkeypatch.setenv("GRAPH_ENABLED", "true")
    monkeypatch.setenv("NEO4J_URI", os.environ["TEST_NEO4J_URI"])
    monkeypatch.setenv(
        "NEO4J_PASSWORD", os.getenv("TEST_NEO4J_PASSWORD", "disposable-fixture")
    )
    monkeypatch.setenv("REDIS_URL", os.environ["TEST_REDIS_URL"])
    monkeypatch.setenv("EXECUTION_REDIS_PREFIX", "graph-api-" + uuid4().hex + ":")
    monkeypatch.setenv("JWT_SECRET", "graph-fixture-jwt-key-with-enough-entropy")
    monkeypatch.setenv("BACKEND_ENV", "development")
    alice = {
        "Authorization": "Bearer "
        + jwt.encode({"sub": "alice"}, os.environ["JWT_SECRET"], algorithm="HS256")
    }
    bob = {
        "Authorization": "Bearer "
        + jwt.encode({"sub": "bob"}, os.environ["JWT_SECRET"], algorithm="HS256")
    }

    async def case():
        # Model the explicit deployment migration, independently of test order.
        from dexter_py.graph.config import GraphConfig
        from dexter_py.graph.repository import GraphRepository
        from dexter_py.graph.migrations import migrate

        bootstrap = await GraphRepository.connect(GraphConfig.from_env())
        try:
            await migrate(bootstrap)
        finally:
            await bootstrap.close()
        async with main.app.router.lifespan_context(main.app):
            repo = main.app.state.graph_repository
            registry = main.app.state.execution_registry
            scope = GraphScope(
                kind="principal", identity=registry.principal("jwt::alice")
            )
            scopes = [scope]
            try:
                entity = GraphEntity(
                    scope=scope,
                    entity_type="Company",
                    canonical_name="Synthetic API fixture",
                )
                await repo.upsert_entity(scope, entity)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=main.app), base_url="http://test"
                ) as client:
                    path = "/api/v1/graph/entities/" + str(entity.id)
                    assert (await client.get(path)).status_code == 401
                    assert (await client.get(path, headers=alice)).status_code == 200
                    assert (await client.get(path, headers=bob)).status_code == 404
                    assert (
                        await client.get(
                            path,
                            headers=alice,
                            params={"known_at": "2024-01-01T00:00:00"},
                        )
                    ).status_code == 422
                    assert (
                        await client.get(
                            path,
                            headers=alice,
                            params={"known_at": "2000-01-01T00:00:00Z"},
                        )
                    ).status_code == 404
                    assert (
                        await client.get(
                            path + "/assertions", headers=alice, params={"limit": 101}
                        )
                    ).status_code == 422
                    assert (
                        await client.get(
                            path + "/assertions",
                            headers=alice,
                            params={"known_at": "2024-01-01T00:00:00"},
                        )
                    ).status_code == 422
                    assert (
                        await client.get("/api/v1/graph/ontology", headers=alice)
                    ).json()["schema_version"] == "1.0"
                    response = await client.post(
                        "/api/v1/platform/workspaces",
                        headers=alice,
                        json={"name": "Graph fixture"},
                    )
                    assert response.status_code == 201
                    workspace = response.json()["id"]
                    shared_scope = GraphScope(kind="workspace", identity=workspace)
                    scopes.append(shared_scope)
                    shared = GraphEntity(
                        scope=shared_scope,
                        entity_type="Company",
                        canonical_name="Synthetic workspace fixture",
                    )
                    await repo.upsert_entity(shared_scope, shared)
                    shared_path = "/api/v1/graph/entities/" + str(shared.id)
                    assert (
                        await client.get(
                            shared_path,
                            headers=alice,
                            params={"workspace_id": workspace},
                        )
                    ).status_code == 200
                    assert (
                        await client.get(
                            shared_path, headers=bob, params={"workspace_id": workspace}
                        )
                    ).status_code in (403, 404)
                    assert (
                        await client.get(shared_path, headers=alice)
                    ).status_code == 404
                    assert (
                        await client.post(path, headers=alice, json={})
                    ).status_code == 405
            finally:
                async with repo._driver.session() as session:
                    for item in scopes:
                        await (
                            await session.run(
                                "MATCH (n {scope:$scope}) DETACH DELETE n",
                                scope=item.key,
                            )
                        ).consume()
                keys = [
                    key
                    async for key in registry.redis.scan_iter(
                        match=registry.prefix + "*"
                    )
                ]
                if keys:
                    await registry.redis.delete(*keys)

    asyncio.run(case())
