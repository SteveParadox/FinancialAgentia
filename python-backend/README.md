# FinancialAgentia Python API

The FastAPI service has a direct LLM streaming endpoint (/query) and a
separate multi-phase Python agent endpoint (/agent/query). The TypeScript
repository retains the more complete financial research tools, which are
not connected to the Python ToolExecutor in this phase.

See [PHASE1_ARCHITECTURE.md](PHASE1_ARCHITECTURE.md) for the actual CLI
execution path, agent/service contracts and deferred integration boundary.

## Local start

From this directory:

    python -m venv .venv
    # Activate .venv for your platform
    python -m pip install -r requirements.txt

Configure OPENAI_API_KEY and, if desired, LLM_MODEL (default gpt-4o-mini).
Do not place secrets in the repository. Then:

    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

Startup does not make a paid LLM call. It fails on invalid agent options or a
configured but unreachable Redis store. GET /health checks liveness;
GET /ready reports dependency configuration, not LLM connectivity.

## Verify

    python -m compileall -q app dexter_py
    python -c "import app.main; print('Backend import successful')"
    python -m pytest -q

With a configured provider and server running, /query provides token SSE
and /agent/query provides an SSE answer/error event and session cookie.
Agent financial tools must be explicitly registered before they can run;
there is no fake production quote tool.

The React Ink CLI currently calls /query, not /agent/query. See the
architecture note before attempting a cross-runtime migration.
