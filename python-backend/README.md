# FinancialAgentia Python backend

The **Python backend is the authoritative executor** for the browser research workspace and the optional Bun/Ink terminal client. Requests arrive at FastAPI, are durably admitted into Redis and executed by a **separate Python worker**. Financial data and model credentials are server-side.

**Current setup guides:**

- [Quick start and Docker local overlay](../docs/getting-started/quick-start.md)
- [Windows PowerShell](../docs/getting-started/windows-setup.md)
- [Linux/macOS](../docs/getting-started/linux-macos-setup.md)
- [Configuration and authentication](../docs/installation/configuration.md)
- [Operations and recovery](../docs/operations/runbook.md)
- [Feature guide](../docs/features/user-guide.md)
- [End-to-end execution internals](END_TO_END_OPERATIONS.md)

## Supported processes

From repository root, prepare Python and browser assets:

```bash
bun install --frozen-lockfile
bun run build:web
python3 -m venv .venv
.venv/bin/python -m pip install -r python-backend/requirements-dev.txt
cp python-backend/.env.example python-backend/.env
```

Configure `python-backend/.env` with Redis, appropriate authentication, model settings and actual financial-provider credentials. Run two separate processes from `python-backend`:

```bash
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# SECOND terminal (also from python-backend):
../.venv/bin/python -m app.execution_worker
```

This manual route needs an independently running persistence-enabled Redis. No public deployment should omit authentication, TLS and Redis protection. The browser is served from `http://127.0.0.1:8000/` once assets are built.

A complete local Docker option is also available: `docker compose -f docker-compose.research.yml -f docker-compose.local.yml up --build -d` from repository root. **The local overlay turns auth off for loopback-only development; never use it publicly.**

## Model check

From `python-backend`:

```bash
../.venv/bin/python scripts/check_model_connection.py
# Opt-in paid call:
../.venv/bin/python scripts/check_model_connection.py --live
```

OpenAI requires `OPENAI_API_KEY` and `LLM_MODEL` but **does not need Ollama**. Ollama local/cloud have distinct settings. For Windows interpreter commands see the dedicated PowerShell guide.

## Internal operations and legacy notes

`END_TO_END_OPERATIONS.md` documents durable execution semantics, Redis migrations, failure recovery and test limits. Older root-level terminal/context documents describe earlier implementations and are **not** authoritative instructions for the current browser-first runtime.
