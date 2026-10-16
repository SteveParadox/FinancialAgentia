# Current state audit — 2026-10-16

Base: `main` at `6a5139a` (merged PR #9). No outstanding PRs at inspection. The three older implementation branches are ancestors of main; the hardening branch was merged. Work continues on `codex/web-research-workspace`.

## Inventory and execution map

| Component | Actual implementation |
| --- | --- |
| Interface | React 19 / Ink / Bun terminal application; no user-facing browser workspace |
| API | Python FastAPI; optional JWT or single shared API key; production requires authentication |
| Jobs/database/queue | Redis hashes, owner sorted-set index, active sorted set, Lua atomic operations, Redis Stream events |
| Worker | Separate Python process, generation fencing, session lease, heartbeat, three-claim limit, deadline |
| Reasoning | Python custom Orchestrator: understand → plan → topological tool batches → reflect → answer |
| Agents | Validated specialist execution records selected from registered tools, rather than autonomous competing agents |
| Models | Official OpenAI SDK, Ollama, existing Anthropic/Google adapters |
| Financial data | Financial Datasets HTTP adapter, bounded retries/cache, no synthetic production fallback |
| Reports | Pydantic numerical evidence, deterministic claim validation, generated JSON schemas, Zod terminal validation |
| Deployment | Redis/API/worker Compose; Python image; no browser build stage |

Incoming authenticated request → admission API → Redis Lua (job + owner index + active queue + event atomically) → worker claim/lease → Python Orchestrator → allowlisted tools → Financial Datasets → deterministic calculations → evidence normalization → claim validation → fenced atomic result/history commit → persisted events/status → terminal renderer. Redis is the authoritative database as well as the queue, so a separate SQL outbox is unnecessary.

The exported TypeScript `Agent` is a backend client, not a second executing orchestrator. Older TS phase/tool modules remain compatibility code and are not invoked by this runtime. No LangGraph runtime is present.

## Confirmed gaps at this base

| Priority | Finding | Evidence / remedy |
| --- | --- | --- |
| P0 | Recovery restarts all reasoning/tool work; saved checkpoints are never read | Worker `_execute` ignores `artifacts`; resume validated completed-plan boundaries |
| P0 | No authorized user retry operation or report-only retrieval endpoint | Execution router has admission/status/cancel/events only; add new linked attempts with atomic single-successor semantics |
| P0 | Worker exits its polling loop on a temporary Redis error | `_run_jobs` and worker pulse have unguarded Redis calls; retry polling, invalidate work on lost heartbeat |
| P0 | Source records have cache labels but no configurable observation freshness policy | Add explicit historical/stale/unverified classification without pretending unknown feed entitlements are live |
| P0 | Ratio normalization gives Sharpe a fraction unit and inherits latest fiscal timestamp across nested records | Unit/date semantics require correction and regression tests |
| P1 | No web workspace, browser refresh journey, browser tests, exports | Add React DOM browser build using existing Bun/React; same-origin static delivery by FastAPI |
| P1 | History omits original question; pagination can stop at expired rows | Add owner-authorized query/metadata and bounded history cleanup |
| P1 | Persisted model output metadata has only model ID; no aggregate per-job model-call reservation | Add explicit bounded invocation/output reservation budget; do not estimate missing billed usage |
| P2 | 24-hour report retention unsuitable as an indefinite archive | Make policy configurable, document durable Redis AOF/backups and retention |

## Existing verified baseline

Actual Redis 7.0.15; Python 3.12.14; Bun 1.4.3. Command: `PYTHONPATH=python-backend TEST_REDIS_URL=redis://127.0.0.1:6389/15 python -m pytest -q python-backend`, with Bun in PATH: **128 passed, 12 opted-in live tests skipped**. This proves deterministic fixtures and local integration, not live entitlements.

## External and financial limitations

No live model/financial credentials or Docker runtime are available. Subscription coverage and commercial permissions must be confirmed by the operator. A single provider cannot constitute independent cross-source verification. Original filing availability, restatements, adjustment entitlement, exchange calendars and point-in-time statements are not fully represented. Historical price research must disclose adjustment and coverage uncertainty; historical fundamental backtests cannot be certified. No production-ready claim is justified by this baseline.

## Migration strategy

Keep existing Redis keys and report version 1, add optional request/successor/configuration fields, preserve history, tolerate old records without new fields. No destructive migration. Continue the additive history-index migration already in the repository. Build the browser as a new client of the same execution API; never put provider keys or orchestration in the browser.
