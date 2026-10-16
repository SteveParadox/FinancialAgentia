# Quick start: FinancialAgentia

This guide is for the **updated browser workspace**, not the historical terminal-only workflow. Two startup options are available.

## Prerequisites

- For the easiest path: Git and Docker Desktop/Engine with the Compose plugin. Use the repository's pinned `python-backend/Dockerfile` to build the runtimes.
- For manual development: Python **3.11+**, Bun **1.4.3+**, and a persistence-enabled Redis **7** instance. These reflect the repository's current documented tooling and Docker images.
- Model access: an authorized OpenAI API key and an available chat-compatible model **or** a reachable compatible Ollama model.
- Financial research: a licensed **FINANCIAL_DATASETS_API_KEY**. Optional SEC facts require validated mappings and an operator contact user agent.

## Docker local development

> **Local machine only.** `docker-compose.local.yml` turns off authentication on the loopback-bound API for a single developer. Do not use this overlay in a public, shared or production deployment.

From a **PowerShell** terminal:

```powershell
git clone https://github.com/SteveParadox/FinancialAgentia.git
cd FinancialAgentia
Copy-Item python-backend\.env.example python-backend\.env
notepad python-backend\.env
```

Set `OPENAI_API_KEY` to your own key, retain `LLM_MODEL=gpt-4o-mini` only if your project can access that model, and set `FINANCIAL_DATASETS_API_KEY`. If using Ollama, follow the [provider guide](../installation/configuration.md#ai-models) instead.

Then:

```powershell
docker compose -f docker-compose.research.yml -f docker-compose.local.yml config
docker compose -f docker-compose.research.yml -f docker-compose.local.yml up -d --build
docker compose -f docker-compose.research.yml -f docker-compose.local.yml ps
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
```

`/health` should return `{"status":"ok"}`. `/ready` should return `ready: true` and HTTP 200 when Redis, a worker, tools and model configuration are ready. It may return **503** if those dependencies are not ready; inspect logs rather than bypassing the check.

Visit **http://127.0.0.1:8000/**. Leave the token field blank **only** in this local development configuration and click **Open workspace**. Submit an initial company request under **Workspace**, with one valid symbol, for example `MSFT`.

To view logs and stop:

```powershell
docker compose -f docker-compose.research.yml -f docker-compose.local.yml logs -f api worker
# Ctrl+C stops following logs only.
docker compose -f docker-compose.research.yml -f docker-compose.local.yml down
```

`down` leaves the named Redis volume intact. **Do not add `-v`** unless you explicitly intend to delete persistent data.

On Linux/macOS, use `cp python-backend/.env.example python-backend/.env` and shell-compatible commands. See [Linux/macOS setup](linux-macos-setup.md).

## Manual development startup

This runs Redis separately and uses **two terminal processes** (API and worker), plus a browser. Commands below assume a POSIX shell; Windows PowerShell equivalents are in [Windows setup](windows-setup.md).

From the repository root:

```bash
bun install --frozen-lockfile
bun run build:web
python3 -m venv .venv
.venv/bin/python -m pip install -r python-backend/requirements-dev.txt
cp python-backend/.env.example python-backend/.env
```

Edit `python-backend/.env`. Configure `REDIS_URL=redis://127.0.0.1:6379/0` and your model and data source credentials. For local development with no auth, leave `JWT_SECRET` and `BACKEND_API_KEY` empty and keep `BACKEND_ENV=development`. Never expose such a service to a public network.

If Redis is not already running, start a **local-only** Redis container:

```bash
docker run -d --name financialagentia-redis -p 127.0.0.1:6379:6379 -v financialagentia-redis-data:/data redis:7.4-alpine redis-server --appendonly yes
```

Terminal A:

```bash
cd python-backend
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Terminal B, from the repository root:

```bash
cd python-backend
../.venv/bin/python -m app.execution_worker
```

Open `http://127.0.0.1:8000/` and connect with a blank token in the local/no-auth configuration. If only the API is running, the research queue will not be processed.

## Authentication and production distinction

The base `docker-compose.research.yml` forces `BACKEND_ENV=production` and `SECURE_COOKIES=true` and requires `JWT_SECRET` or `BACKEND_API_KEY`. **The browser accepts a bearer JWT or a blank token in unsecured local development. It does not have a shared API-key login or an account registration page.** Multi-user workspaces require distinct provisioned JWT subjects; a shared API key represents one principal. See [configuration](../installation/configuration.md#authentication-and-browser-access) and [deployment](../deployment/production-guide.md).

## First success checklist

1. `/health` responds with HTTP 200.
2. `/ready` reports Redis and worker readiness.
3. The browser page and bundled CSS/JS load.
4. `Open workspace` connects using the configured authentication.
5. The server model is shown as configured.
6. A test research job progresses past queued status.
7. A report appears with source evidence or explicit limitations.
8. Research history remains accessible after reconnecting.

A successful health check is **not** proof of paid LLM access or licensed financial-provider connectivity. See [verification](../testing/verification.md) for controlled tests.
