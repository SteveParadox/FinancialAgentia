# Environment and startup configuration

> **Current canonical instructions:** [Quick start](../getting-started/quick-start.md), [configuration reference](../installation/configuration.md), [Windows PowerShell setup](../getting-started/windows-setup.md), [Linux/macOS setup](../getting-started/linux-macos-setup.md), and [production deployment](production-guide.md).

The current application serves built React browser assets with FastAPI, and runs one or more Python worker processes against persistence-enabled Redis. The base `docker-compose.research.yml` selects production-like auth requirements; the separate `docker-compose.local.yml` overlay is **strictly for loopback development with no authentication**.

From repository root, build browser assets via `bun install --frozen-lockfile` and `bun run build:web`. From `python-backend`, run `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` and `python -m app.execution_worker` in **separate processes** using the same backend `.env` and reachable Redis.

The actual env template is `python-backend/.env.example`. OpenAI requires a server-side key and selected model; it does not require Ollama. Ollama local/cloud are separately configured. Financial Datasets needs licensed credentials; SEC EDGAR is optional with correctly mapped issuers. Neither `/health` nor `/ready` tests paid-provider entitlement. `/ready` requires a configured model, tool registration, Redis and a recent worker heartbeat.

For the full production, upgrade, alert, workspace and retention guidance, follow the linked canonical guides instead of prior terminal-only instructions.
