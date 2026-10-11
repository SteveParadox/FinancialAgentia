# FinancialAgentia Phase 1: API architecture and verified boundaries

## Dependency map (source inspection)

Current Bun CLI:
    src/cli.tsx → useAgentExecution.ts → POST http://localhost:8000/query
    → FastAPI direct LLM stream → SSE token events → CLI rendering.

The more complete TypeScript research path exists in source but is **not**
currently invoked by the CLI hook:
    src/agent/orchestrator.ts → understand → plan →
    TaskExecutor/ToolExecutor → src/tools/finance/*, src/tools/search/* →
    reflect → answer → TypeScript MessageHistory.

The Python API's distinct agent route:
    POST /agent/query → auth + session lookup → Python Orchestrator.run →
    structured understand → validated plan → ExecutePhase →
    registered Python tools → structured reflection → streamed LLM answer
    → response as SSE answer event → session-store commit.

## Ownership decision

* Bun/TypeScript owns the existing financial data adapters and research tools.
  Preserve them unchanged. The current CLI transport still calls /query,
  which uses direct LLM completion, not the TypeScript research engine.
* FastAPI owns HTTP contracts, request authentication, sessions, cookies,
  rate limits, metrics, logging and SSE framing.
* The Python Orchestrator owns query-scoped agent coordination, but is not
  declared equivalent to the TypeScript financial research engine.
* ExecutePhase is the **only Python tool execution implementation**.
  The legacy Python TaskExecutor delegates to it.
* InMemorySessionStore/RedisSessionStore and MessageHistory own persistence;
  the API commits a turn once the agent finishes successfully.
* Both language implementations remain, but no cross-language execution
  bridge or duplicate financial API integrations are added in Phase 1.

## Backend startup

From the repository root:

    cd python-backend
    python -m venv .venv

Activate the virtual environment using your shell, then:

    python -m pip install -r requirements.txt
    # Configure OPENAI_API_KEY and optionally LLM_MODEL=gpt-4o-mini
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

Import convention: run from python-backend and use absolute imports
(dexter_py.*). Do not run python-backend/app/main.py as a script or
modify sys.path in the production app.

LLM_MODEL is an explicit model/provider choice. Supported provider prefixes
are gpt-/o1/o3/o4 (OpenAI), claude- (requires optional Anthropic package),
gemini- (requires optional Google package), and ollama-. No provider fallback
is performed when the chosen model or credentials are unavailable.

Configuration:
* LLM_MODEL: selected provider model, default gpt-4o-mini.
* AGENT_MAX_ITERATIONS: 1-10, default 3; invalid values prevent startup.
* OPENAI_API_KEY / ANTHROPIC_API_KEY / GOOGLE_API_KEY / OLLAMA_API_KEY:
  required according to the selected model. Local Ollama does not need a key.
* REDIS_URL: optional. If configured, Redis must be reachable and the redis
  dependency must be installed; startup fails instead of silently switching
  to memory.
* JWT_SECRET and BACKEND_API_KEY: preserve the existing authentication policy.
* SECURE_COOKIES: cookie Secure attribute, default false for local development.

Startup constructs one shared Orchestrator and ToolExecutor. A fresh
ExecutionContext is allocated for every query; conversation context is read
from the specified MessageHistory, not stored on the shared orchestrator.
Concurrent requests for one session are serialized within a single API
process. Distributed sessions require cross-process locking in a later phase.

GET /health is a liveness signal. GET /ready separately reports whether core
components were initialized and whether the configured model provider appears
configured. It does NOT contact a paid provider or certify financial-tool
coverage. The financial_tools_registered field is false until real Python
tools are explicitly registered.

## Contracts

Orchestrator(model: str, max_iterations: int = 3,
             llm_client=None, tool_executor=None)

    await orchestrator.run(query: str, message_history=None) -> str

* Model and iteration limits are validated at construction.
* message_history must support format_for_planning() if supplied.
* Orchestrator never persists session history or returns a fabricated
  "successful" answer on underlying failures.
* Unknown/missing/cyclic/duplicate dependencies cause validation errors
  before tools execute.
* Every tool call has a timeout; each task makes at most
  1 + max_retries attempts.
* Skipped tasks and failed tool results are not counted as completed.
* API /agent/query returns one SSE answer event after the agent completes.
  It retains the existing event shape: type, content, session_id, request_id.
  Failures return an SSE event with type=error and do not commit history.
* /query remains the existing direct LLM streaming endpoint with token/role/
  request_id event shape. The CLI still consumes this legacy endpoint.

## Tests and verification commands

From python-backend (in an isolated environment):

    python -m compileall -q app dexter_py
    python -c "import app.main; print('Backend import successful')"
    python -m pytest -q
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

The tests use mocked LLM and tool dependencies to exercise the full
understand → plan → execute → reflect → answer lifecycle and the real
FastAPI SSE/session route without network access.

From repository root:

    bun install --frozen-lockfile
    bun run typecheck
    bun test

Do not assert these commands succeeded until executed. TestClient validates
ASGI lifespan, but a real Uvicorn startup and network request remain required
for an operational acceptance check.

## Deferred work

1. Connect an authoritative financial research engine to an explicit API
   contract. TypeScript already contains the richer financial tool catalog;
   avoid reimplementing its APIs in Python before choosing the integration.
2. The CLI's /query currently bypasses full research. Decide whether to
   restore in-process TypeScript orchestration or add a versioned backend
   agent transport after independent integration tests.
3. Replace dormant Python phase experiments only after confirming downstream
   callers and migration compatibility.
4. Provider-specific live network validation, financial tool parity,
   production multi-worker session locking, longer-duration testing and
   advanced deployment/security hardening are outside Phase 1.

Phase 1 must **not** be declared production-financial-research complete
merely because API startup, deterministic mocks, and basic agent routing work.
