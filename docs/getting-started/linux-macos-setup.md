# Linux and macOS installation

Use a POSIX-style shell. This guide covers a manual development installation. For a containerized application, follow [quick start](quick-start.md) with `cp` instead of PowerShell `Copy-Item`.

## Prerequisites

Install and verify Git, Python **3.11+**, Bun **1.4.3+**, and Redis 7 or Docker:

```bash
git --version
python3 --version
bun --version
docker compose version
```

Use your system's supported package management and platform-specific Python installation instructions. Do not replace your system Python. The repository Dockerfile pins Bun 1.4.3 and Python 3.11.

## Install

From your chosen project folder:

```bash
git clone https://github.com/SteveParadox/FinancialAgentia.git
cd FinancialAgentia
bun install --frozen-lockfile
bun run build:web
python3 -m venv .venv
.venv/bin/python -m pip install -r python-backend/requirements-dev.txt
cp python-backend/.env.example python-backend/.env
```

Edit `python-backend/.env` in your preferred editor. Configure Redis, the selected LLM and financial dataset credentials. For local-only development, keep `BACKEND_ENV=development` and leave both auth keys empty.

If you do not already have Redis, use Docker:

```bash
docker run -d --name financialagentia-redis -p 127.0.0.1:6379:6379 -v financialagentia-redis-data:/data redis:7.4-alpine redis-server --appendonly yes
docker exec financialagentia-redis redis-cli ping
```

If the container already exists and is stopped, start it with `docker start financialagentia-redis`.

## Run two processes

In **terminal A**, from repository root:

```bash
cd python-backend
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In **terminal B**, from repository root:

```bash
cd python-backend
../.venv/bin/python -m app.execution_worker
```

Verify in a third terminal:

```bash
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/ready
```

Open **http://127.0.0.1:8000/**. No separate `bun dev` web server is required for this supported path; FastAPI serves built browser assets.

## Stopping

Use Ctrl+C in the worker and API terminals. Use `docker stop financialagentia-redis` when done. The named volume remains.

For local Ollama, make sure the backend can reach the configured endpoint. For containerized applications, `127.0.0.1` refers to the container, not the host; consult [configuration](../installation/configuration.md#ai-models).
