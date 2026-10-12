from fastapi import FastAPI, Request, HTTPException, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
from typing import AsyncGenerator, Optional
from contextlib import asynccontextmanager
from weakref import WeakValueDictionary
import asyncio
import os
import json
import uuid
import time
import hashlib
import hmac
from collections import deque

# Load environment variables
from dotenv import load_dotenv
import os
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

# Structured logging
import structlog

# Rate limiting
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware
from slowapi.errors import RateLimitExceeded

# Metrics
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST

from dexter_py.model.llm import call_llm_stream
from dexter_py.utils.session_store import get_session_store
from dexter_py.agent.orchestrator import Orchestrator
from dexter_py.agent.tool_executor import ToolExecutor
from dexter_py.tools.financial_datasets import register_financial_tools
from dexter_py.agent.execution_registry import ExecutionRegistry
from dexter_py.utils._utils import LLMConfig, configure_llm, close_llm_clients


# Configure structlog for JSON output
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger("dexter_backend")


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Construct dependencies once, fail visibly, clean up when stopped."""
    logger.info("backend_starting")
    if os.getenv("BACKEND_ENV", "development").lower() == "production":
        if not (os.getenv("JWT_SECRET") or os.getenv("BACKEND_API_KEY")):
            raise RuntimeError("Production backend requires JWT_SECRET or BACKEND_API_KEY")
        if os.getenv("SECURE_COOKIES", "false").lower() != "true":
            raise RuntimeError("Production requires SECURE_COOKIES=true")
    application.state.recent_requests = deque(maxlen=1000)
    application.state.session_locks = WeakValueDictionary()
    application.state.session_store = get_session_store()
    application.state.execution_registry = None
    if os.getenv("REDIS_URL"):
        shared = ExecutionRegistry.from_env()
        if not await shared.health():
            raise RuntimeError("Durable execution Redis is unavailable")
        application.state.execution_registry = shared
    elif os.getenv("BACKEND_ENV", "development").lower() == "production":
        raise RuntimeError("Production financial research requires REDIS_URL")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    iterations = int(os.getenv("AGENT_MAX_ITERATIONS", "3"))
    configure_llm(LLMConfig(default_model=model))
    # Real tools are registered explicitly; the Python scaffold has no
    # production financial feed. Never register demonstration prices here.
    tools = ToolExecutor()
    financial_provider = register_financial_tools(tools)
    application.state.financial_provider = financial_provider
    application.state.orchestrator = Orchestrator(
        model=model, max_iterations=iterations, tool_executor=tools,
    )
    logger.info("backend_initialized", model=model)
    try:
        yield
    finally:
        for dependency in (application.state.session_store, tools, financial_provider,
                           application.state.execution_registry):
            if dependency is None:
                continue
            closer = getattr(dependency, "close", None)
            if callable(closer):
                outcome = closer()
                if asyncio.iscoroutine(outcome):
                    await outcome
        await close_llm_clients()
        logger.info("backend_shutdown")


app = FastAPI(title="Dexter Python Backend", lifespan=lifespan)

# Browser origins are explicit because wildcard + credentialed CORS is unsafe.
allowed_origins = [
    value.strip() for value in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173,"
        "http://127.0.0.1:3000,http://127.0.0.1:5173",
    ).split(",") if value.strip()
]
if "*" in allowed_origins:
    raise ValueError("CORS_ORIGINS must list exact origins, not '*'")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiter (10 requests per minute per remote address by default)
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


# Prometheus metrics
REQUEST_COUNT = Counter("dexter_requests_total", "Total requests received")


class Query(BaseModel):
    prompt: str = Field(min_length=1, max_length=10000)

    @field_validator("prompt")
    @classmethod
    def reject_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    state = app.state
    initialized = all(hasattr(state, key) for key in ("session_store", "orchestrator"))
    initialized = initialized and state.orchestrator.execute_phase.tool_executor is not None
    model = state.orchestrator.model if initialized else ""
    provider_configured = (
        bool(os.getenv("ANTHROPIC_API_KEY")) if model.startswith("claude-") else
        bool(os.getenv("GOOGLE_API_KEY")) if model.startswith("gemini-") else
        bool(os.getenv("OLLAMA_API_KEY")) if model.startswith("ollama-") and model.endswith("-cloud") else
        True if model.startswith("ollama-") else
        bool(os.getenv("OPENAI_API_KEY")) if model.startswith(("gpt-", "o1", "o3", "o4")) else
        False
    )
    tools_registered = (len(state.orchestrator.execute_phase.tool_executor.tools)
                        if initialized and hasattr(state.orchestrator.execute_phase.tool_executor, "tools") else 0)
    shared = getattr(state, "execution_registry", None)
    shared_ready = bool(shared is not None and await shared.health())
    ready = bool(initialized and provider_configured and tools_registered and shared_ready)
    return JSONResponse(
        {"ready": ready, "agent_initialized": bool(initialized),
         "model_provider_configured": bool(provider_configured),
         "financial_tools_registered": bool(tools_registered),
         "durable_execution_registry_ready": shared_ready},
        status_code=200 if ready else 503,
    )


@app.get("/api/v1/tools")
async def registered_tools(request: Request):
    await require_auth(request)
    executor = request.app.state.orchestrator.execute_phase.tool_executor
    signatures = getattr(executor, "tool_signatures", {})
    return {"tools": [{"name": name, "signature": signature}
                      for name, signature in sorted(signatures.items())]}


@app.get("/metrics")
async def metrics(request: Request):
    await require_auth(request)
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


@app.get("/api/recent")
async def api_recent(request: Request):
        """Return authorized operational metadata without prompt contents."""
        await require_auth(request)
        items = list(getattr(app.state, 'recent_requests', []))
        # Convert timestamps to ISO for readability
        out = []
        for it in items:
                o = dict(it)
                try:
                        o['ts_iso'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(o.get('timestamp', 0)))
                except Exception:
                        o['ts_iso'] = None
                out.append(o)
        return JSONResponse(content={"recent": out})


@app.get("/dashboard")
async def dashboard(request: Request):
        """Serve the operational dashboard only to authorized callers."""
        await require_auth(request)
        html = r'''
<!doctype html>
<html>
    <head>
        <meta charset="utf-8" />
        <title>Dexter Dashboard</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; }
            table { border-collapse: collapse; width: 100%; }
            th, td { border: 1px solid #ddd; padding: 8px; }
            th { background: #f4f4f4; }
            pre { background: #111; color: #0f0; padding: 10px; }
        </style>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    </head>
    <body>
        <h1>Dexter Dashboard</h1>
        <p>Recent requests (most recent first)</p>
        <table id="recent-table">
            <thead><tr><th>Time</th><th>Path</th><th>Method</th><th>Status</th><th>Request ID</th><th>Prompt (snippet)</th></tr></thead>
            <tbody></tbody>
        </table>

    <h2>Metrics</h2>
    <pre id="metrics">Loading...</pre>

    <h2>Per-client request durations</h2>
    <canvas id="clientChart" width="800" height="200"></canvas>

        <script>
                    async function loadRecent() {
                try {
                    const res = await fetch('/api/recent');
                    const data = await res.json();
                    const tbody = document.querySelector('#recent-table tbody');
                    tbody.innerHTML = '';
                    for (const r of data.recent) {
                        const tr = document.createElement('tr');
                        tr.innerHTML = `<td>${r.ts_iso || ''}</td><td>${r.path || ''}</td><td>${r.method || ''}</td><td>${r.status || ''}</td><td>${r.request_id || ''}</td><td>${(r.prompt||'').replace(/</g,'&lt;')}</td>`;
                        tbody.appendChild(tr);
                    }
                            updateClientChart(data.recent);
                } catch (e) {
                    console.error(e);
                }
            }

            async function loadMetrics() {
                try {
                    const res = await fetch('/metrics');
                    const text = await res.text();
                    document.getElementById('metrics').textContent = text;
                } catch (e) {
                    document.getElementById('metrics').textContent = 'Failed to load metrics: ' + e;
                }
            }

            async function refresh() {
                await Promise.all([loadRecent(), loadMetrics()]);
            }

                    // Chart.js setup
                    let clientChart = null;
                    function updateClientChart(recent) {
                        try {
                            // Aggregate by client host
                            const byClient = {};
                            for (const r of recent) {
                                const client = r.client || 'unknown';
                                if (!byClient[client]) byClient[client] = [];
                                if (typeof r.duration_ms === 'number') byClient[client].push(r.duration_ms);
                            }
                            const labels = Object.keys(byClient);
                            const avg = labels.map(l => {
                                const arr = byClient[l];
                                if (!arr || arr.length === 0) return 0;
                                return Math.round(arr.reduce((a,b)=>a+b,0)/arr.length);
                            });

                            if (!clientChart) {
                                const ctx = document.getElementById('clientChart').getContext('2d');
                                // eslint-disable-next-line no-undef
                                clientChart = new Chart(ctx, {
                                    type: 'bar',
                                    data: {
                                        labels,
                                        datasets: [{ label: 'Avg duration (ms)', data: avg, backgroundColor: 'rgba(54,162,235,0.5)' }]
                                    },
                                    options: { responsive: true, maintainAspectRatio: false }
                                });
                            } else {
                                clientChart.data.labels = labels;
                                clientChart.data.datasets[0].data = avg;
                                clientChart.update();
                            }
                        } catch (e) {
                            console.error('chart update failed', e);
                        }
                    }

                    setInterval(refresh, 3000);
                    refresh();
        </script>
    </body>
</html>
'''
        return Response(content=html, media_type='text/html')


def _verify_jwt(token: str) -> dict:
    """Verify a JWT token using JWT_SECRET (HS256) and return the payload.

    If `JWT_SECRET` is not set, this function will raise a RuntimeError.
    """
    from jose import jwt, JWTError

    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET not configured")
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def require_auth(request: Request) -> None:
    """Authentication dependency:
    - If `JWT_SECRET` is set, expect `Authorization: Bearer <token>` and validate JWT.
    - Otherwise, if `BACKEND_API_KEY` is set, accept `X-API-Key: <value>` header.
    - If neither configured, no auth is required (development mode).
    """
    jwt_secret = os.getenv("JWT_SECRET")
    if jwt_secret:
        auth = request.headers.get("authorization") or request.headers.get("Authorization")
        if not auth or not auth.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Authorization header missing or invalid")
        token = auth.split(" ", 1)[1]
        payload = _verify_jwt(token)
        # attach payload to request state for handlers
        request.state.user = payload
        return

    expected = os.getenv("BACKEND_API_KEY")
    if not expected:
        # No auth configured
        return
    provided = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _session_storage_key(request: Request, session_id: str) -> str:
    """Bind sessions to JWT subject; API key mode is one shared principal.

    A shared API key is one account, not a multi-user identity system.
    """
    try:
        parsed = uuid.UUID(session_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid session ID")
    if str(parsed) != session_id:
        raise HTTPException(status_code=400, detail="Invalid session ID")
    claims = getattr(request.state, "user", None)
    if claims is not None:
        subject = claims.get("sub") or claims.get("user_id")
        if not subject:
            raise HTTPException(status_code=401, detail="JWT subject required")
        principal = f"jwt:{claims.get('iss', '')}:{subject}"
    else:
        principal = "shared-api-key"
    namespace = hashlib.sha256(principal.encode("utf-8")).hexdigest()
    return f"{namespace}:{session_id}"


def _session_lock(request: Request, storage_key: str) -> asyncio.Lock:
    locks = request.app.state.session_locks
    lock = locks.get(storage_key)
    if lock is None:
        lock = asyncio.Lock()
        locks[storage_key] = lock
    return lock


@app.middleware("http")
async def add_request_id_and_count(request: Request, call_next):
    # Add a request id and increment metrics
    rid = str(uuid.uuid4())
    request.state.request_id = rid
    REQUEST_COUNT.inc()
    # Add request_id to structlog context for the duration of the request
    structlog.contextvars.bind_contextvars(request_id=rid)
    start = time.time()
    try:
        response = await call_next(request)
    finally:
        structlog.contextvars.clear_contextvars()
    # Record a minimal request entry (prompt is recorded in /query)
    try:
        entry = {
            "timestamp": time.time(),
            "method": request.method,
            "path": request.url.path,
            "status": getattr(response, 'status_code', None),
            "request_id": rid,
            "duration_ms": int((time.time() - start) * 1000),
            "client": None,
        }
        # Append to rolling deque
        app.state.recent_requests.appendleft(entry)
    except Exception:
        pass
    response.headers["X-Request-Id"] = rid
    return response


@app.post("/query")
@limiter.limit("10/minute")
async def query(request: Request, q: Query):
    """Stream the LLM response for the given prompt as Server-Sent Events (SSE).

    Each token/chunk is framed as an SSE `data:` event containing a JSON
    object with fields like `token`, `role`, and `request_id`.
    """
    # Enforce authentication if configured
    await require_auth(request)

    request_id = getattr(request.state, "request_id", None)

    # Log received data
    logger.info("received_query", prompt_length=len(q.prompt), request_id=request_id)

    # Never keep unredacted financial questions in public request telemetry.

    async def event_stream() -> AsyncGenerator[bytes, None]:
        try:
            async for chunk in call_llm_stream(q.prompt):
                if chunk is None or chunk == "":
                    continue
                payload = {
                    "token": chunk,
                    "role": "assistant",
                    "request_id": request_id,
                }
                s = f"data: {json.dumps(payload)}\n\n"
                # Log sent data
                logger.debug("streamed_token", token_length=len(chunk), request_id=request_id)
                yield s.encode("utf-8")
        except Exception as e:
            logger.exception("Error during LLM streaming", error=str(e))
            err_payload = {"error": "LLM request failed", "request_id": request_id}
            err = f"data: {json.dumps(err_payload)}\n\n"
            yield err.encode("utf-8")

    return StreamingResponse(event_stream(), media_type="text/event-stream; charset=utf-8")


# ============================================================================
# Session-based Agent Endpoints
# ============================================================================

class AgentQuery(BaseModel):
    """Request body for agent query with session support."""
    query: str = Field(min_length=1, max_length=10000)

    @field_validator("query")
    @classmethod
    def reject_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value
    session_id: Optional[str] = None  # Auto-generated if not provided


@app.post("/api/v1/agents/execute")
@app.post("/agent/query")
@limiter.limit("10/minute")
async def agent_query(request: Request, q: AgentQuery):
    """Run agent query with session-based conversation context.
    
    Maintains conversation history per session using cookies or provided session ID.
    Each request loads the session's message history, runs the agent with context,
    and updates the store with the new turn.
    
    Query Parameters:
    - session_id: Optional session ID (UUID). If not provided, generates new one.
    
    Returns:
        SSE stream with agent response chunks
    """
    await require_auth(request)
    if getattr(request.app.state, "execution_registry", None) is not None:
        raise HTTPException(
            status_code=409,
            detail="Use /api/v1/agents/executions for durable financial research")
    if os.getenv("BACKEND_ENV", "development").lower() == "production":
        raise HTTPException(status_code=503, detail="Durable financial research unavailable")
    
    request_id = getattr(request.state, "request_id", None)
    
    # Determine session ID: from param, cookie, or generate new
    session_id = q.session_id
    if not session_id:
        session_id = request.cookies.get("session_id")
    if not session_id:
        session_id = str(uuid.uuid4())
    
    logger.info(
        "Agent query received",
        query_length=len(q.query),
        session_id=session_id,
        request_id=request_id,
    )
    
    # Get session store and orchestrator
    session_store = request.app.state.session_store
    orchestrator = request.app.state.orchestrator
    storage_key = _session_storage_key(request, session_id)
    lock = _session_lock(request, storage_key)
    
    async def event_stream() -> AsyncGenerator[bytes, None]:
        try:
            async with lock:
                history = session_store.get(storage_key)
                if not history._model:
                    history.set_model(orchestrator.model)
                final_answer = await orchestrator.run(
                    query=q.query,
                    message_history=history,
                )
                # Only persist completed answers, never failed attempts.
                await history.add_message(q.query, final_answer)
                session_store.set(storage_key, history)
            
            # Stream the final answer
            payload = {
                "type": "answer",
                "content": final_answer,
                "session_id": session_id,
                "request_id": request_id,
            }
            s = f"data: {json.dumps(payload)}\n\n"
            logger.debug("Streaming agent answer", request_id=request_id)
            yield s.encode("utf-8")
            
        except Exception as e:
            logger.exception("Error during agent query", error=str(e), session_id=session_id)
            err_payload = {
                "type": "error",
                "error": "Agent execution failed",
                "session_id": session_id,
                "request_id": request_id,
            }
            err = f"data: {json.dumps(err_payload)}\n\n"
            yield err.encode("utf-8")
    
    # Create response with session ID in cookie
    response = StreamingResponse(event_stream(), media_type="text/event-stream; charset=utf-8")
    response.set_cookie(
        key="session_id",
        value=session_id,
        max_age=86400,  # 24 hours
        secure=os.getenv("SECURE_COOKIES", "false").lower() == "true",
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/agent/history")
async def get_history(request: Request, session_id: Optional[str] = None):
    """Get conversation history for a session.
    
    Query Parameters:
    - session_id: Optional session ID (defaults to cookie)
    
    Returns:
        JSON with conversation history, turns count, and model info
    """
    await require_auth(request)
    
    # Determine session ID
    session_id = session_id or request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    
    session_store = request.app.state.session_store
    storage_key = _session_storage_key(request, session_id)
    async with _session_lock(request, storage_key):
        history = session_store.get(storage_key)
        snapshot = history.get_messages()
        model = history._model

    if not snapshot:
        return JSONResponse({
            "session_id": session_id,
            "turns": 0,
            "messages": [],
        })
    
    messages = [
        {
            "id": msg.id,
            "query": msg.query,
            "answer": msg.answer[:500],  # Truncate for preview
            "summary": msg.summary,
        }
        for msg in snapshot
    ]
    
    return JSONResponse({
        "session_id": session_id,
        "turns": len(snapshot),
        "messages": messages,
        "model": model,
    })


@app.delete("/agent/history")
async def clear_history(request: Request, session_id: Optional[str] = None):
    """Clear conversation history for a session.
    
    Query Parameters:
    - session_id: Optional session ID (defaults to cookie)
    
    Returns:
        JSON confirming deletion
    """
    await require_auth(request)
    
    # Determine session ID
    session_id = session_id or request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    
    session_store = request.app.state.session_store
    storage_key = _session_storage_key(request, session_id)
    async with _session_lock(request, storage_key):
        session_store.delete(storage_key)
    
    logger.info("Session history cleared", session_id=session_id)
    
    return JSONResponse({
        "session_id": session_id,
        "status": "cleared",
    })


@app.post("/agent/session")
async def create_session(request: Request):
    """Create a new session and return the session ID.
    
    Useful for explicit session creation before first query.
    
    Returns:
        JSON with new session ID and cookie
    """
    await require_auth(request)
    
    session_id = str(uuid.uuid4())
    logger.info("New session created", session_id=session_id)
    
    response = JSONResponse({
        "session_id": session_id,
        "status": "created",
    })
    response.set_cookie(
        key="session_id",
        value=session_id,
        max_age=86400,
        secure=os.getenv("SECURE_COOKIES", "false").lower() == "true",
        httponly=True,
        samesite="lax",
    )
    return response


# Mount after require_auth is defined; single durable API, shared execution registry.
from app.execution_api import router as execution_router
app.include_router(execution_router)
