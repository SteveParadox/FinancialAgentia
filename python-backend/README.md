# FinancialAgentia Python backend

The supported research path is the Bun/Ink terminal frontend → versioned FastAPI
admission API → Redis registry → Python worker → source-backed financial tools →
validated research report. Python is the sole executor. The exported TypeScript
`Agent` is a compatibility transport, not a separate tool runner.

See [END_TO_END_OPERATIONS.md](END_TO_END_OPERATIONS.md) for setup, validation,
upgrade/recovery, limits, contracts, and the exact verification boundary.
Older Phase 1 documents describe previous versions and are not deployment guides.

## Start

```sh
python -m venv .venv
# Activate the virtual environment for your shell.
python -m pip install -r requirements.txt
cp .env.example .env
# Fill the Redis URL, authentication, model and provider credentials.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# Separate terminal, same environment:
python -m app.execution_worker
```

From the repository root, run `bun install --frozen-lockfile`, set the matching
client authentication from `env.example`, then `bun start`. `/history` lists saved
executions; `/resume <execution ID>` retrieves a finished report or follows an
active job. Ctrl+C while running requests server cancellation. Exiting or losing
transport does not itself cancel a durable job.

`/health` is liveness. `/ready` requires registered tools, configured model,
reachable Redis and a recent worker heartbeat; it does not prove paid-provider
entitlements or LLM connectivity. Live smoke tests are opt-in.
