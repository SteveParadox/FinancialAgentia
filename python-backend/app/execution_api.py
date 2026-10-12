"""Durable, authenticated agent executions. Worker is a separate process."""
from __future__ import annotations

import json
import os
import re
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from dexter_py.agent.execution_registry import ExecutionConflict, ExecutionRegistry, TERMINAL

router = APIRouter(prefix="/api/v1/agents/executions", tags=["financial research executions"])


async def registry_and_principal(request: Request) -> tuple[ExecutionRegistry, str]:
    # Keep all auth mechanisms aligned with legacy endpoints.
    from app.main import require_auth
    await require_auth(request)
    registry = getattr(request.app.state, "execution_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Durable execution requires REDIS_URL")
    claims = getattr(request.state, "user", None)
    if claims is not None:
        subject = claims.get("sub") or claims.get("user_id")
        if not subject:
            raise HTTPException(status_code=401, detail="JWT subject required")
        identity = f"jwt:{claims.get('iss', '')}:{subject}"
    else:
        identity = "shared-api-key"
    return registry, registry.principal(identity)


class Admission(BaseModel):
    query: str = Field(min_length=1, max_length=10000)
    session_id: str | None = None

    @field_validator("query")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Research query must not be blank")
        return value


@router.post("", status_code=202)
async def admit(request: Request, body: Admission):
    registry, principal = await registry_and_principal(request)
    token = request.headers.get("Idempotency-Key")
    if not token:
        raise HTTPException(status_code=400, detail="Idempotency-Key required")
    try:
        session_id = str(uuid.UUID(body.session_id)) if body.session_id else str(uuid.uuid4())
        execution_id, created = await registry.admit(
            principal=principal, session_id=session_id, query=body.query,
            idempotency_key=token,
            model=request.app.state.orchestrator.model,
            deadline_seconds=int(os.getenv("AGENT_EXECUTION_DEADLINE_SECONDS", "300")),
        )
    except ExecutionConflict:
        raise HTTPException(status_code=409, detail="Idempotency key conflict")
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid research admission parameters")
    return {
        "execution_id": execution_id, "session_id": session_id,
        "created": created, "state": (await registry.get(execution_id, principal))["state"]
    }


@router.get("/{execution_id}")
async def status(execution_id: str, request: Request):
    registry, principal = await registry_and_principal(request)
    try:
        record = await registry.get(execution_id, principal)
    except ValueError:
        record = None
    if record is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    return record


@router.post("/{execution_id}/cancel")
async def cancel(execution_id: str, request: Request):
    registry, principal = await registry_and_principal(request)
    try:
        state = await registry.cancel(execution_id, principal)
    except ValueError:
        state = "NOT_FOUND"
    if state == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="Execution not found")
    return {"execution_id": execution_id, "state": state,
            "cancel_accepted": state in {"CANCEL_REQUESTED", "CANCELLED"}}


@router.get("/{execution_id}/events")
async def events(execution_id: str, request: Request):
    registry, principal = await registry_and_principal(request)
    try:
        initial = await registry.get(execution_id, principal)
    except ValueError:
        initial = None
    if initial is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    last_event_id = request.headers.get("last-event-id", "0-0")
    if not re.fullmatch(r"\d+-\d+", last_event_id):
        raise HTTPException(status_code=400, detail="Malformed Last-Event-ID")

    async def stream():
        async for event_id, fields in registry.event_stream(
                execution_id, principal, last_event_id):
            payload = {
                "protocol_version": 1,
                "execution_id": execution_id, "event_id": event_id,
                "type": "execution." + fields["state"].lower(),
                "state": fields["state"],
            }
            yield ("id: " + event_id + "\ndata: " + json.dumps(payload) + "\n\n").encode()
        # Retrieve terminal truth even if the Redis stream was trimmed.
        final = await registry.get(execution_id, principal)
        if final and final["state"] in TERMINAL:
            output = {
                "execution_id": execution_id,
                "session_id": final["session_id"],
                "type": "answer" if final["state"] == "SUCCEEDED" else "error",
            }
            if final["state"] == "SUCCEEDED":
                output["content"] = final.get("result", {}).get("answer", "")
                output["report"] = final.get("result", {})
            else:
                output["error"] = final["state"]
            yield ("data: " + json.dumps(output) + "\n\n").encode()

    return StreamingResponse(stream(), media_type="text/event-stream; charset=utf-8")
