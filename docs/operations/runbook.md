# Operations, worker management, backup and upgrades

This guide assumes the supported stack: **FastAPI + browser build, independent Python worker, shared Redis**. See [quick start](../getting-started/quick-start.md).

## Service map

| Service | Role |
| --- | --- |
| `api` | HTTP, auth, web static assets, request admission, reports, platform APIs |
| `worker` | Durable research claims, model/tool calls, job recovery, monitoring evaluation |
| `redis` | Job hashes, queue/streams, leases, saved platform objects and TTL state |

The base `docker-compose.research.yml` launches all three. No PostgreSQL/MySQL database is required by this implementation.

## Everyday Docker commands

From repository root, for **local-only development**:

```bash
docker compose -f docker-compose.research.yml -f docker-compose.local.yml up -d
docker compose -f docker-compose.research.yml -f docker-compose.local.yml ps
docker compose -f docker-compose.research.yml -f docker-compose.local.yml logs --tail=100 api worker redis
docker compose -f docker-compose.research.yml -f docker-compose.local.yml restart api worker
docker compose -f docker-compose.research.yml -f docker-compose.local.yml down
```

For a properly secured production-like Compose setup, **omit** `-f docker-compose.local.yml`; the base file forces production environment and requires valid auth. Production additionally needs a reverse proxy and managed operator controls.

A change in `python-backend/.env` requires recreating containers, not only `restart`:

```bash
docker compose -f docker-compose.research.yml -f docker-compose.local.yml up -d --force-recreate
```

A change to application source/build requires `up -d --build`. Check `ps` and `logs` afterward.

## Readiness and diagnostics

- `GET /health`: API process liveness. HTTP 200 does not prove workers, provider entitlement, or successful research.
- `GET /ready`: Redis, heartbeat from at least one worker, registered tools, configured LLM; returns HTTP 503 if not ready.
- `GET /api/v1/model`: authenticated, shows server model selection/configuration only.
- `GET /api/v1/platform/providers`: authenticated provider capability/configuration, not a successful live quote test.
- `GET /api/v1/platform/me`: authenticated principal identity.

For local Docker:

```bash
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/ready
docker compose -f docker-compose.research.yml -f docker-compose.local.yml logs --tail=150 worker
```

Persistent worker claims use leases and fencing; killed workers can be replaced after lease expiry. A job may repeat external read calls after a crash, so exactly-once paid provider billing is **not** guaranteed. Research TTL defaults to 24 hours. Portfolio/workspace/rule objects persist until deleted; temporary saved context, invitations and notifications have an independent TTL (default 90 days).

## Research API smoke test

When local authentication is disabled (loopback only), the API can be exercised as follows:

```bash
curl -i -X POST http://127.0.0.1:8000/api/v1/agents/executions \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: doc-smoke-001' \
  --data '{"query":"Analyze Microsoft using reported financial evidence.","scope":{"category":"company","symbols":["MSFT"]}}'
```

The endpoint responds 202 with an `execution_id`. Fetch `GET /api/v1/agents/executions/{execution_id}`, then `GET /api/v1/agents/executions/{execution_id}/result` after success. A generated ID must be used, not literally the placeholder. If authentication is enabled, pass the required `Authorization: Bearer <JWT>` or trusted `X-API-Key` header as appropriate. Research can fail with insufficient evidence or provider errors; this is not a guaranteed live smoke-test success.

Progress events: `GET /api/v1/agents/executions/{execution_id}/events`. The browser uses SSE with reconnection and a polling fallback. Cancelling an active job uses `POST /api/v1/agents/executions/{execution_id}/cancel`; retry eligibility is determined server-side.

## Redis backups

Redis is the authoritative store and holds both expiring and longer-lived platform data. The reference Compose service uses an AOF-backed named volume (`research-redis`). Back up Redis **before** an upgrade. Do not rely only on the presence of an AOF file; configure a repeatable backup and restoration process.

For a consistent maintenance backup, pause writers (API and worker) using an appropriate maintenance window; save an AOF/RDB snapshot using Redis-supported tooling and securely copy persistent files/volume according to your Redis deployment. Test restoration to a *separate* Redis instance and verify job/platform indexes without overwriting the active store.

**Never run `docker compose down -v` as a routine restart or repair**; `-v` deletes the named Redis volume. Note that AOF everysec may lose approximately the last second of writes on abrupt host failure; set stronger policies if required.

## Upgrading

1. Check branch and local changes: `git status`, `git branch --show-current`, `git log -1 --oneline`.
2. Back up `python-backend/.env` securely and back up Redis. Confirm recoverability.
3. Stop or drain worker processes and prevent new submissions during migration.
4. Update the intended branch with `git pull --ff-only` only if working tree is clean; do not overwrite local changes.
5. Compare current `python-backend/.env` with `python-backend/.env.example` and supply new required settings.
6. Inspect release notes, migrations, container configuration and dependency changes.
7. The earlier execution-index migration `python-backend/scripts/migrate_execution_index.py` remains in the repository. Review its preconditions; if required for upgrading an old Redis dataset, run its dry run, then `--apply` **before** restoring worker traffic:

   ```bash
   cd python-backend
   ../.venv/bin/python scripts/migrate_execution_index.py
   ../.venv/bin/python scripts/migrate_execution_index.py --apply
   ```

   For Windows use `..\.venv\Scripts\python.exe`. Docker operators must run the script in an appropriate container/maintenance environment with access to the same Redis and with no old workers running. Do not assume it is necessary for every upgrade.
8. Manual install: reinstall dependencies and run `bun install --frozen-lockfile` / `bun run build:web`; restart API and worker with identical settings. Docker: rebuild, recreate and validate all affected services.
9. Check `/health`, `/ready`, representative authorized research, previously saved reports, portfolio/monitoring/workspace records and worker logs.
10. Validate alerts and scheduled jobs after returning the worker to service.

**Rollback:** Stop new workers, restore compatible application images/source and environment, and apply an explicitly validated data rollback if required. Never blindly restore an older Redis snapshot while newer workers are active. Preserve persistent research records, scheduled rules and workspaces.

## Operational limitations

Research history and intermediate jobs are not a permanent audit archive unless retention and backups are designed accordingly. In-app notifications have no external delivery transport. Price alerts use periodic UTC polling. Independent production tests of LLM entitlements, market provider licensing, backups, reverse proxy and resilience are deployment responsibilities.

See [troubleshooting](troubleshooting.md), [production](../deployment/production-guide.md) and [verification](../testing/verification.md).
