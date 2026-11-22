"""Read-only foundation endpoints. No extraction, arbitrary Cypher or verification API."""

from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from dexter_py.graph.models import (
    GraphAssertion,
    GraphEntity,
    GraphOntologyDefinition,
    GraphScope,
)
from dexter_py.graph.repository import GraphError, GraphRepository
from .platform_api import dependencies

router = APIRouter(prefix="/api/v1/graph", tags=["financial knowledge graph"])


async def scoped_repository(
    request: Request, workspace_id: UUID | None
) -> tuple[GraphRepository, GraphScope]:
    store, actor = await dependencies(request)
    scope = GraphScope(kind="principal", identity=actor)
    if workspace_id:
        await store.workspace(str(workspace_id), actor)
        scope = GraphScope(kind="workspace", identity=str(workspace_id))
    repo = getattr(request.app.state, "graph_repository", None)
    if repo is None:
        raise HTTPException(503, "Graph functionality is disabled")
    return repo, scope


@router.get("/ontology")
async def ontology(request: Request) -> GraphOntologyDefinition:
    await dependencies(request)
    return GraphOntologyDefinition()


@router.get("/entities/{identity}")
async def entity(
    request: Request,
    identity: UUID,
    workspace_id: UUID | None = None,
    known_at: datetime | None = None,
) -> GraphEntity:
    if known_at is not None and (
        known_at.tzinfo is None or known_at.utcoffset() is None
    ):
        raise HTTPException(422, "Historical timestamps must include timezone")
    repo, scope = await scoped_repository(request, workspace_id)
    result = await repo.get_entity(scope, identity, known_at=known_at)
    if result is None:
        raise HTTPException(404, "Graph entity not found")
    return result


@router.get("/entities/{identity}/assertions")
async def assertions(
    request: Request,
    identity: UUID,
    workspace_id: UUID | None = None,
    known_at: datetime | None = None,
    valid_at: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, list[GraphAssertion]]:
    for value in (known_at, valid_at):
        if value and (value.tzinfo is None or value.utcoffset() is None):
            raise HTTPException(422, "Historical timestamps must include timezone")
    repo, scope = await scoped_repository(request, workspace_id)
    return {
        "items": await repo.assertions_for(
            scope, identity, known_at=known_at, valid_at=valid_at, limit=limit
        )
    }


async def graph_error_handler(request: Request, exc: GraphError) -> JSONResponse:
    from dexter_py.graph.repository import GraphConflict, GraphIntegrityError

    status = (
        409
        if isinstance(exc, GraphConflict)
        else 422
        if isinstance(exc, GraphIntegrityError)
        else 503
    )
    return JSONResponse({"detail": str(exc)}, status_code=status)
