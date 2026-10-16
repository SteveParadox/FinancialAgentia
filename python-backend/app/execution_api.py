"""Durable, authenticated agent executions. Worker is a separate process."""
from __future__ import annotations

import json
import os
import re
import uuid

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from datetime import date, datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dexter_py.agent.execution_registry import ExecutionConflict, ExecutionRegistry, ExecutionCapacity, RetryIneligible, TERMINAL
from dexter_py.agent.financial_evidence import ResearchReport

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


class ResearchScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["company", "comparison", "historical_prices", "general"] = "general"
    symbols: list[str] = Field(default_factory=list, max_length=5)
    start_date: date | None = None
    end_date: date | None = None

    @field_validator("symbols")
    @classmethod
    def symbols_valid(cls, value):
        from dexter_py.tools.financial_datasets import validate_ticker
        normalized = [validate_ticker(item) for item in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Symbols must be distinct")
        return normalized

    @model_validator(mode="after")
    def scope_valid(self):
        if self.category == "comparison" and len(self.symbols) < 2:
            raise ValueError("Comparison requires at least two symbols")
        if self.category == "company" and len(self.symbols) != 1:
            raise ValueError("Company research requires one symbol")
        if self.category == "historical_prices" and (not self.symbols or not self.start_date or not self.end_date):
            raise ValueError("Historical price research requires symbols and dates")
        if bool(self.start_date) != bool(self.end_date):
            raise ValueError("Supply both dates")
        if self.start_date and self.category != "historical_prices":
            raise ValueError("Dated scope is supported for historical prices only")
        if self.start_date and not self.start_date < self.end_date <= datetime.now(timezone.utc).date():
            raise ValueError("Dates must form a past interval")
        return self


class Admission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=10000)
    session_id: str | None = None
    scope: ResearchScope | None = None
    context_id: uuid.UUID | None = None

    @field_validator("query")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Research query must not be blank")
        return value


@router.post("", status_code=202)
async def admit(request: Request, body: Admission):
    registry, principal = await registry_and_principal(request)
    tools = request.app.state.orchestrator.execute_phase.tool_executor
    if not getattr(tools, "tools", {}):
        raise HTTPException(status_code=503, detail="Financial research tools are not configured")
    options = body.scope.model_dump(mode="json") if body.scope else {}
    if body.context_id:
        from app.platform_store import PlatformStore
        await PlatformStore(registry).context(principal, str(body.context_id))
        options["context_id"] = str(body.context_id)
    token = request.headers.get("Idempotency-Key")
    if not token:
        raise HTTPException(status_code=400, detail="Idempotency-Key required")
    try:
        session_id = str(uuid.UUID(body.session_id)) if body.session_id else str(uuid.uuid4())
        execution_id, created = await registry.admit(
            principal=principal, session_id=session_id, query=body.query,
            idempotency_key=token,
            model=request.app.state.orchestrator.model,
            request_options=options,
            request_session_id=body.session_id,
            max_active=int(os.getenv("RESEARCH_MAX_ACTIVE_PER_USER", "5")),
            deadline_seconds=int(os.getenv("AGENT_EXECUTION_DEADLINE_SECONDS", "300")),
        )
    except ExecutionCapacity:
        raise HTTPException(status_code=429, detail="Active research limit reached", headers={"Retry-After": "10"})
    except ExecutionConflict:
        raise HTTPException(status_code=409, detail="Idempotency key conflict")
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid research admission parameters")
    record = await registry.get(execution_id, principal)
    return {
        "protocol_version": 1,
        "execution_id": execution_id, "session_id": record["session_id"],
        "created": created, "state": record["state"]
    }


@router.post("/{execution_id}/retry", status_code=202)
async def retry(execution_id: str, request: Request):
    registry, principal = await registry_and_principal(request)
    if not getattr(request.app.state.orchestrator.execute_phase.tool_executor, "tools", {}):
        raise HTTPException(status_code=503, detail="Financial research tools are not configured")
    token = request.headers.get("Idempotency-Key")
    if not token:
        raise HTTPException(status_code=400, detail="Idempotency-Key required")
    try:
        child, created = await registry.retry(
            execution_id, principal, token, request.app.state.orchestrator.model,
            max_active=int(os.getenv("RESEARCH_MAX_ACTIVE_PER_USER", "5")),
            deadline_seconds=int(os.getenv("AGENT_EXECUTION_DEADLINE_SECONDS", "300")))
    except LookupError:
        raise HTTPException(status_code=404, detail="Execution not found")
    except ExecutionCapacity:
        raise HTTPException(status_code=429, detail="Active research limit reached", headers={"Retry-After": "10"})
    except (ExecutionConflict, RetryIneligible):
        raise HTTPException(status_code=409, detail="Execution cannot be retried or a retry already exists")
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid retry parameters")
    record = await registry.get(child, principal)
    return {"protocol_version": 1, "execution_id": child, "session_id": record["session_id"],
            "created": created, "state": record["state"], "parent_execution_id": execution_id}


@router.get("/{execution_id}/result", response_model=ResearchReport)
async def result(execution_id: str, request: Request):
    registry, principal = await registry_and_principal(request)
    try:
        record = await registry.get(execution_id, principal)
    except ValueError:
        record = None
    if record is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    if record["state"] not in TERMINAL or not record.get("result"):
        raise HTTPException(status_code=409, detail="A persisted report is not available")
    return record["result"]


@router.get("")
async def history(request: Request, limit: int = Query(20, ge=1, le=100),
                  before: float = Query(float("inf"), gt=0), cursor: str | None = None):
    registry, principal = await registry_and_principal(request)
    try:
        records = await registry.list_jobs(principal, limit, before, cursor)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid history cursor")
    return {"protocol_version": 1, "executions": records,
            "next_cursor": records[-1]["execution_id"] if len(records) == limit else None,
            "next_before": records[-1]["created_at"] if len(records) == limit else None}


@router.post("/cancel-by-key")
async def cancel_by_key(request: Request):
    registry, principal = await registry_and_principal(request)
    idem_key = request.headers.get("Idempotency-Key")
    if not idem_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key required")
    try:
        state = await registry.cancel_by_key(principal, idem_key)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid Idempotency-Key")
    if state == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="Execution not found")
    return {"state": state, "cancel_accepted": state in {
        "CANCEL_PENDING", "CANCEL_REQUESTED", "CANCELLED"}}


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
            if await request.is_disconnected():
                return
            if event_id is None:
                yield b": heartbeat\n\n"
                continue
            payload = {
                "protocol_version": 1,
                "execution_id": execution_id, "event_id": event_id,
                "type": fields.get("type") or "execution." + fields["state"].lower(),
                "state": fields["state"],
                "phase": fields.get("phase"),
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
                output["error"] = final.get("failure_code") or final["state"]
                if final.get("result"):
                    output["report"] = final["result"]
            yield ("data: " + json.dumps(output) + "\n\n").encode()

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
    })
