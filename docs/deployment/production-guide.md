# Production deployment: prerequisites and release gates

**This is an operator checklist, not a certification that the repository is production-ready.** The browser, API and worker can be built and tested with fixtures, but live model/data entitlements, TLS, identity issuance, Redis backup restoration and sustained load require deployment-specific verification.

## Architecture

Build from repository root using `python-backend/Dockerfile`. The multi-stage image compiles the React browser with Bun and serves it with FastAPI. The same image runs the separate Python research worker. `docker-compose.research.yml` starts `api`, `worker` and a persistence-enabled `redis`. Redis is the current authoritative store; it is not PostgreSQL.

### Secure base Compose

The base Compose file sets `BACKEND_ENV=production`, `SECURE_COOKIES=true` and `REDIS_URL=redis://redis:6379/0`. It requires an actual authentication mechanism configured in `python-backend/.env`. Use **valid JWTs with stable distinct subjects** for browser-based multi-user workspaces. The application has no signup/token issuer; deploy an authorized identity issuer and configure the expected signing secret/issuer/audience.

Do **not** deploy `docker-compose.local.yml`. That file removes authentication and is only intended for a developer's loopback machine.

## Network and secrets

- Place the browser/API behind a trusted HTTPS reverse proxy. The Compose example binds API only to `127.0.0.1:8000`; protect this listener.
- Configure TLS, secure cookies and exact allowed CORS origins. Do not use `*`.
- Keep OpenAI/Ollama/Financial Datasets keys and `JWT_SECRET` in an operator-managed secret store or restricted server env files; never in browser bundles.
- Secure Redis with ACL/password and TLS where appropriate. The reference local Compose Redis service is **not** a hardened network production service.
- Allow SSE streaming without reverse-proxy buffering; preserve `Last-Event-ID` and set proxy read timeouts long enough for heartbeats.
- Ensure model and licensed provider egress access satisfies provider terms.
- Set operational budgets, resource limits, telemetry and explicit alerting for job/backlog/provider failures.

## Workers and persistence

API and all worker instances must share the same Redis primary/namespace and model settings. Workers process one job at a time, with bounded task concurrency inside each job. Use graceful termination, leases and recovery; avoid rolling out old non-fenced workers alongside new code.

Choose Redis AOF, backups, restore testing, monitoring, retention, eviction policy and disk capacity consistent with the risk of losing ongoing research, portfolios, workspaces and monitoring rules. The Compose volume alone is not an off-host backup.

## Ready for launch checklist

- [ ] Real identity provider issues correctly scoped, expiring browser JWTs; unauthorized access tests pass.
- [ ] HTTPS and reverse-proxy streaming verified.
- [ ] Redis protected, persistent, backed up and restoration rehearsed.
- [ ] Model configuration verified with **optional real** model call; usage budget applied.
- [ ] Licensed financial source tested for actual entitled instruments and dates.
- [ ] `/ready` reports Redis, worker, registered tools and model ready.
- [ ] Full real research journey produces a report with provenance and limitations.
- [ ] A worker restart is recovered; queued work is not stranded.
- [ ] Monitoring schedules and notifications behave with expected UTC polling.
- [ ] Portfolios and workspace permissions isolate different JWT subjects.
- [ ] Upgrade/rollback rehearsal completed without data loss.
- [ ] Container/base-image vulnerability and load tests pass in the target environment.

Read [configuration](../installation/configuration.md), [runbook](../operations/runbook.md), [verification](../testing/verification.md), and the existing [deployment checklist](production-checklist.md).
