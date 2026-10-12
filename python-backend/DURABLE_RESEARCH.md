# Durable financial research (experimental deployment)

**PR dependency notice:** PR #6 was merged into `main`; PR #7 was merged into
`codex/phase1-python-backend-repair`, so its financial research code must be
integrated into main by this follow-up PR. A closed PR is not proof that its base
branch was main.

## Components

Python FastAPI owns authenticated execution admission; Redis owns authoritative
execution state, event streams, idempotency, session locks, worker leases, fencing
generations, completed answers and conversation turns. Dedicated
`python -m app.execution_worker` processes compete for work. The Bun CLI sends
an idempotency key, polls authoritative status and calls the server's cancellation
endpoint. LLM answers are never published verbatim without deterministic
financial-evidence verification; incomplete proof returns a PARTIAL or
INSUFFICIENT_EVIDENCE report.

## Start

Install `python-backend/requirements.txt`, set `REDIS_URL`,
`BACKEND_API_KEY` (or `JWT_SECRET`), `FINANCIAL_DATASETS_API_KEY`, the
model's credential, and `LLM_MODEL`. Run:

```sh
cd python-backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# In a separate process with the same Redis URL and credentials:
python -m app.execution_worker
```

For a local container stack, configure `python-backend/.env` and run
`docker compose -f docker-compose.research.yml up --build`.
This stack is a reference staging setup, not a fully secured production
cluster. Put authenticated TLS in front of the backend and use a secured
Redis service (TLS, ACL, backups and persistence) across machines.

## API

- `POST /api/v1/agents/executions` with `Idempotency-Key` and JSON
  `{"query":"...","session_id":"optional-UUID"}`.
- `GET /api/v1/agents/executions/{id}` retrieves the authoritative state.
- `POST /api/v1/agents/executions/{id}/cancel` requests server cancellation.
- `GET /api/v1/agents/executions/{id}/events` returns persisted SSE events and
  a terminal answer/error. Reconnect with `Last-Event-ID`.
- `/agent/query` remains available in development without Redis only; it is
  blocked when shared execution is enabled to prevent a competing runtime.
- The CLI uses the new durable endpoint, not `/agent/query`.

## Safety and limits

Worker lease is 20 seconds, renewed approximately every second. Expired leases
can be reclaimed; generation fencing blocks stale final commits. At-least-once
external provider execution is possible after crashes. Cancellation stops
local cancellable work but cannot undo provider requests already delivered.
Redis hash + stream retention and idempotency retention default to 24 hours;
conversation history retains up to 20 turns with the same TTL.

**Important:** Redis persistence with every-second AOF still permits up to
approximately one second of loss under crash. It is not an exactly-once
durability guarantee. Set more conservative fsync/replication options according
to the production SLA.

Research status is authoritative. The optional untrusted AI claim draft is
validated against evidence IDs, numerical values and symbols. Missing sources,
missing quote timestamps and inconsistent numbers are rejected rather than
presented as verified. Not all news, filings, or derived ratios can currently
be converted to structured claims. Such work remains explicitly partial.

## Tests

```sh
cd python-backend
python -m compileall -q app dexter_py tests
python -m pytest -q
TEST_REDIS_URL=redis://127.0.0.1:6379/15 python -m pytest -q tests/test_execution_registry_redis.py
FINANCIAL_RESEARCH_LIVE_TESTS=true FINANCIAL_DATASETS_API_KEY=<secret> python -m pytest -q tests/test_live_provider.py
cd ..
bun run typecheck && bun test
```

Live tests are explicitly opt-in and must not be treated as passed when skipped.
Do not supply provider secrets in source control or shell histories.

## Outstanding release gates

- Real controlled Redis multi-worker/worker-crash and cancellation soak testing.
- Confirmed live Financial Datasets entitlements, provider schema and LLM calls.
- Deterministic evidence extraction beyond selected numerical fields, including
  filing/news source-level provenance and derived metric lineage.
- Production secret management, TLS, Redis failover and backup/restore drills.
- Complete CI execution: a GitHub workflow with empty job steps or missing logs
  does not prove passing tests.
