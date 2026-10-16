> Historical report for merged PR #9. The subsequent web workspace, recovery, financial validation and security implementation is documented in [docs/implementation-report.md](docs/implementation-report.md). Do not treat the counts or release status below as the latest verification.

# FinancialAgentia engineering report — 15 October 2026

Latest follow-up validation: **128 Python tests passed; 14 Bun tests passed**.
See section L for the implementation review and OpenAI connection changes.
The original-delivery baseline and results below are retained for traceability.

## A. Initial architecture assessment

Inspected default branch `main` at
`e8fbc7a78f21b388ed5a3c19babdc9922fd633db` (merge of PR #8). GitHub reported no
open PRs at the start of this change. The application is a React/Ink terminal
client on Bun, a Python FastAPI API, a separate Python execution worker, Redis
hashes/streams/Lua transactions and Financial Datasets/LangChain integrations.
There is no browser research frontend or SQL ORM/migration stack to repair.
The earlier AgentOptions, orchestrator interface and per-request context repairs
were already present; this change does not claim them as newly discovered bugs.

The active UI already called durable Python admission. A second exported
TypeScript Agent still ran an independent tool loop. Python owned the supported
research execution, while legacy development endpoints and old documentation
still described different execution paths.

## B. Root causes and reproducible baseline

1. **Clean install failed:** slowapi 0.1.4 required Redis <4 while the execution
   registry required Redis >=5. This prevented the documented/Docker pip install.
2. **Admission retry conflict:** an omitted session was replaced by a fresh UUID
   before hashing the request, so retries of identical requests could conflict.
3. **Cancellation recovery loss:** claiming code removed an unexpired
   CANCEL_REQUESTED job from its recovery queue. A dead owner could leave it stuck.
4. **Queue scan starvation:** only the first 256 active entries were inspected,
   without updating their order after claims/renewals or session-lock contention.
5. **Evidence gaps:** combined statements and calculated ratios were not fully
   normalized, timestamp checks were incomplete, and matching a number could
   certify unrelated model-written assertions surrounding it.
6. **Configuration and UI gaps:** no durable job-list API or terminal resume
   command; old TypeScript orchestration remained callable; readiness could raise
   on Redis failure and did not require a live worker.
7. **LLM budgets:** LangChain invocation branches did not forward max_tokens and
   temperature, despite accepting those options. Parse logging included content.
8. **Validation drift:** frozen dependencies installed, but typechecking failed
   because fetch mocks omitted Bun's static preconnect member and a closure value
   was narrowed incorrectly.

After resolving the installer conflict, the first Python baseline was 61 passed,
2 failed, 17 skipped. Both failures were HTTPX 0.27 rejecting the environment's
socks5h ALL_PROXY while constructing a provider. The provider now explicitly
honors the HTTPS proxy. Initial Bun tests: 10 passed, with typechecking failing.

## C. Implemented corrections

- `execution_registry.py`: stable omitted-session request fingerprint; correct
  stored session on idempotent response; bounded admission, per-owner history,
  limited claims, queue-order maintenance, fenced checkpoints, worker heartbeats,
  terminal cancellation consistency and idle SSE heartbeats. Retained existing
  state vocabulary and Redis storage rather than introducing new infrastructure.
- `execution_api.py`: history with bounded pagination, 429 admission responses,
  stable retry response, ownership checks, replay/terminal reports, no-buffer SSE
  headers and disconnect cleanup.
- `execution_worker.py` / `orchestrator.py`: intermediate understanding, plans,
  specialist task records and provider results persisted under generation fencing;
  structured agent summaries; evidence-bound reports; explicit insufficient-data
  failure; shutdown recovery and classified worker diagnostics.
- `financial_evidence.py` / `contracts.py`: versioned agent/report schemas,
  nested fundamentals, numerical input lineage, unit handling, dated attributed
  news, source deduplication and deterministic claim rendering. Cancelled jobs
  cannot retain a raced successful report.
- `financial_datasets.py` / `calculations.py`: stricter instrument, timestamp,
  date-range, nested company and currency checks; truthful cache/adjustment
  metadata; bounded jittered retries; concurrent-request coalescing; deterministic
  risk calculations and input lineage. API options follow reviewed provider docs.
- `llm.py` / `llm_integration.py`: output limits actually reach LangChain calls;
  bounded repair validation; parse logs exclude content; configuration readiness
  also requires the matching SDK.
- `main.py`: dependency failures produce 503 readiness; worker-presence check;
  production JWT expiry, optional issuer/audience validation; dashboard values use
  DOM textContent instead of request-derived innerHTML.
- Bun transport/hook/CLI: bounded response reads, validated versioned report
  contract, retrievable history, `/resume <ID>`, bounded transport retries and
  explicit server cancellation. Exported TypeScript Agent now delegates to Python.
- Dependency/config/docs: compatible slowapi, declared Python >=3.11 (required by
  asyncio.timeout), explicit frontend/server environment examples, executable
  terminal build, additive history-index upgrade, CI schema/lint/E2E/build gates.

## D. AI research capabilities and boundaries

Planning, real tool selection/execution, reflection, synthesis and numerical
verification run through the authoritative Python lifecycle. Typed task records
identify market, fundamental, news, quantitative, risk or general research roles.
These roles share the task engine; they are not seven independent LLM services.

Registered quantitative tools cover historical-price risk metrics, SMA/simple
RSI, net margin and period-end ROE. Deterministic primitives additionally test
returns, compounding, CAGR, volatility, drawdown, Sharpe, correlation, ratios and
benchmark-relative performance. The last two comparison primitives are not a
fully evaluated benchmark/scenario agent feature.

A model draft is untrusted. Verified numerical claims render from actual metric
fields with units, dates and evidence IDs; their surrounding AI prose is not
certified by a matching value. Missing data yields partial/failed research, not
invented financial facts. News remains attributed publisher headlines, with no
claim that sentiment/speculation is established fact. Rich prose synthesis,
conflict resolution and point-in-time filing availability remain incomplete.

## E. Provider status

| Integration | Verified here | Live status / credentials |
| --- | --- | --- |
| Financial Datasets quotes/history | HTTP adapter, identity/time validation, errors, cache, coalescing and calculations against fixtures | Blocked: no configured entitled live API key |
| Statements/fundamentals/ratios | Structured parsing, nested identity, periods/currency and lineage against fixtures | Blocked: live entitlements/schema response not tested |
| Company news | Provider mapping, dated headline attribution/deduplication and bounded results | Blocked: no live API validation |
| Filings/company profile/metrics/comparison | Existing real adapters retained; registration and selected fixtures tested | Live smoke tests skipped |
| OpenAI/default model | Routing, request budgets, stream adapter and structured model port tested | No paid model call performed |
| Claude/Gemini/Ollama | Routing/config probes and selected mock adapter tests | Optional SDKs/local service and credentials must be provisioned; not live verified |

Production registers no mock financial feed. Test-only fixture modules explicitly
replace external HTTP/model IO. No subscription access, currency, exchange,
adjustment policy, model usage or dollar cost was invented.

## F. Verified frontend/backend journey

A socket-level test starts real Uvicorn with the real Redis registry and worker,
uses deterministic external HTTP/model fixtures, submits from the Bun client,
executes the Python planner/tools/calculations, persists a typed report, retrieves
history, reconnects by execution ID and renders the report through the actual
Ink AnswerBox component using real process streams. A separate integration test
checks progress/event replay and unauthorized status/stream/cancel/history access.

This verifies the terminal application's connected vertical slice. It is not a
browser UI test or a paid-provider production deployment test. No unrequested web
frontend was introduced.

## G. Test evidence

Environment: Python 3.12.14, Bun 1.4.3, Redis 7.0.15 locally. CI targets Python 3.11
and Redis 7.4. After publishing PR #9, both hosted workflows failed before any
job step started. GitHub annotation: "The job was not started because your
account is locked due to a billing issue." Hosted CI therefore did not execute;
these local results must not be represented as passing hosted checks.

Commands run from `python-backend` unless specified otherwise:

| Command | Actual result |
| --- | --- |
| `python -m pip install -r requirements.txt` in fresh virtualenv, after correction | Succeeded; initial conflicting install failure retained in baseline notes |
| `python -m pip install -r requirements-dev.txt` | Succeeded |
| `python -m pip check` | No broken requirements |
| `TEST_REDIS_URL=redis://127.0.0.1:6389/15 python -m pytest -q` with Bun on PATH | **101 passed, 12 skipped, 187 warnings in 8.29s** |
| `ruff check .` | Passed configured syntax, undefined-name and control-flow checks across backend |
| `python -m compileall -q app dexter_py tests scripts` | Passed |
| `PYTHONPATH=. python scripts/export_contracts.py --check` | Passed; generated Pydantic schemas match committed specs |
| Root: `bun install --frozen-lockfile` | Passed, no dependency changes |
| Root: `bun run typecheck` | Passed, includes socket-E2E TSX code |
| Root: `bun test` | **12 passed, 0 failed**, 24 expectations |
| Root: `bun run build` | Passed, 24 local modules; runtime packages external; Bun/dependencies remain required |
| `git diff --check` and changed-file secret-pattern scan | Passed / no matching secret patterns |

Relevant final pytest log: `101 passed, 12 skipped, 187 warnings in 8.29s`.
Skipped tests are the 12 existing explicitly opted-in live-provider cases.
Warnings are retained; they include legacy Pydantic, HTTPX and datetime
API deprecations and were not suppressed to obtain a passing result.

Real Redis tests cover simultaneous duplicate admission, competing claims,
capacity/rate limiting, remote cancellation, cancellation versus success, expired
leases, stale checkpoint/final-write rejection, bounded recovery attempts,
260-job queue fairness, durable event replay, and once-only history commits.
A separate child process claims a lease, is killed, and a new owner safely
reclaims after expiry. The history upgrade is tested for repeatability and
preservation of representative existing job fields/TTLs.

The full final test run includes successful, missing-data, rate-limit, malformed
model, cancellation, unauthorized-access, reconnect, independent numeric
reference and killed-owner cases. It does not include a production-scale
worker/reverse-proxy/Redis-failover soak.

## H. Security findings

Resolved request-derived dashboard markup injection, incomplete owner checks
around newly added history, production JWT expiry omission, optional issuer/
audience enforcement, unbounded new-job admission, lost cancellation recovery,
report retention after cancellation, response-read memory growth and raw LLM
parse-content logging. Existing tool allowlisting, fixed provider host,
credentialed CORS origin allowlist and server-side job ownership remain in force.

Limits are server enforced and cross-process in Redis. API-key mode intentionally
represents one account; use JWT subjects for individual ownership. No signup or
token issuer was added. This was not a full independent dependency penetration
test or a claim that every legacy optional/demo module is production hardened.

## I. Deployment readiness

The documented dependency install, startup, real HTTP path, workers, schemas,
additive index upgrade and terminal build are locally verified. `/health` and
`/ready` have separate meanings, and Redis outages/absent workers cannot report
ready. Worker shutdown leaves leases recoverable.

Docker is not available in this environment. The Docker build, actual Compose
startup, production TLS proxy, Redis persistence/failover, backup/restore and paid
external services were **not verified**. Their absence prevents a production-ready
claim. The reference Compose configuration is staging guidance, not an already
secured deployment. No deployment, migration on user data, or merge was executed.

## J. Remaining work

| Priority | Remaining item | Reason |
| --- | --- | --- |
| P0 release gate | Restore GitHub Actions account billing and rerun hosted workflows | Jobs have no runner/steps and the annotation reports an account billing lock; mandatory hosted checks have not passed |
| P0 release gate | Live provider/model smoke tests using actual credentials and entitlements | Numerical/provider and model correctness cannot be established by fixtures alone |
| P0 release gate | Build/run Docker stack in deployment environment; TLS, Redis ACL/persistence/backups and identity-token configuration | Required infrastructure and operational guarantees are not available here |
| P1 | Full point-in-time research with filing publication availability and historical corporate-action policy | Current date validation does not establish historically knowable fundamentals; backtesting remains unsupported |
| P1 | Rich specialist coverage, benchmark/scenario integration, conflict-aware synthesis and evaluation corpus | Typed tool roles and deterministic reports do not establish broad investment-research quality |
| P1 | Aggregate token/provider-cost accounting and per-job monetary budgets | Output caps, call limits and quotas exist; measured end-to-end spend accounting does not |
| P1 | Long-term report archive and production fault/streaming soak tests | Current retention is 24 hours; local recovery tests do not prove operational SLA |
| P2 | Broader dependency/security review, optional provider SDK matrix and legacy/demo cleanup | Targeted application hardening is not a comprehensive supply-chain audit |
| P2 | Expanded job/provider metrics, SDK usage and tracing; deprecation upgrades | Worker diagnostics exist, but the full requested operations dashboard is not implemented |

The user's full production-readiness checklist is therefore **not fully complete**.
This delivery implements and verifies a substantial connected repair while
explicitly leaving the above acceptance gates open.

## K. Git delivery

Implementation branch: `codex/end-to-end-research-hardening`, based on the main
commit recorded above. Published draft PR: https://github.com/SteveParadox/FinancialAgentia/pull/9

- `f41f117ffe86f97e33423a413dc75178da5dcd17`: implementation and regression tests.
- `9e6efc1d9ea2eab68178024e2bf88fd03070ff68`: operations/validation documentation.

The shell push lacked GitHub credentials. Publication succeeded through the
connected GitHub account, and the remote trees matched the tested local trees
exactly (`c56bc884603b6ccec3d45856a0b11940da797c35` before this report addendum).
This report addendum changes documentation only. Hosted checks remain blocked
as described above. No merge or production rollout occurred.


## L. Follow-up implementation review and OpenAI connection

OpenAI routing existed in the first delivery, but its LangChain request path
unconditionally sent legacy max_tokens and temperature parameters. Reasoning
models could reject those requests. The follow-up uses the already installed
official OpenAI SDK directly and keeps the shared completion/streaming interface.
It adds max_completion_tokens, reasoning-family message/sampling compatibility,
explicit openai/<model-id> routing, normal completion/refusal checks, bounded
retries and deterministic transport cleanup. No Ollama service is required.

Additional confirmed/corrected implementation gaps:

- A broad TypeError fallback could issue another model request for an internal
  adapter error; it now propagates without duplicate execution.
- Connection/server errors did not consistently enter the transient retry class.
  Typed SDK exceptions now normalize to bounded retry categories; 401 is not
  retried, and SDK retries are disabled so budgets do not multiply.
- Raw provider error bodies and structured validation error values could leak
  into exception text. They are now normalized without echoing keys/model data.
- A restarted worker could execute an admitted job under its current model
  rather than its persisted model. It now fails MODEL_CONFIGURATION_CHANGED
  before performing external work when these differ.
- A timestamp-shaped fiscal period could pass a preliminary date check and then
  crash evidence normalization. The malformed row is rejected without discarding
  other valid records.
- The exported TypeScript Agent waited for remote cancellation before aborting
  local transport. It now interrupts locally first and prevents late publication.
- The terminal /model command gave configuration instructions but did not show
  server truth. It now reads authenticated GET /api/v1/model; provider keys remain
  on the backend. The unused frontend API-key readiness hook was disconnected.

Follow-up validation in the same Python/Bun/Redis versions recorded above:

| Command / check | Result |
| --- | --- |
| Real Redis + `PYTHONPATH=python-backend python -m pytest -q python-backend`, Bun on PATH | **128 passed, 12 skipped, 231 warnings in 16.99s** |
| `bun run typecheck` | Passed |
| `bun test` | **14 passed, 0 failed; 28 expectations** |
| `bun run build` | Passed; 23 local modules, external runtime dependencies |
| `ruff check python-backend` | Passed configured targeted checks |
| `scripts/export_contracts.py --check` | Passed; financial wire schema unchanged |
| Dependency installation / `pip check` | Passed / no broken requirements |
| `python python-backend/scripts/check_model_connection.py` | provider=openai, configured=false, live_verified=false |

Tests exercise actual SDK request encoding, authorization headers, JSON parsing,
SSE decoding, output limits, reasoning/classic model options, malformed/truncated/
refused responses, bounded retries and resource closure through mocked HTTP.
A real Redis/API/worker/orchestrator test additionally completes a research job
through that SDK transport, retrieves financial fixtures, verifies evidence and
persists the report while Ollama is unavailable. These are not paid live calls.

No OpenAI API key was available in the implementation environment. Live
connection validation remains blocked. The configuration-only check makes no
network request. `scripts/check_model_connection.py --live` is an explicit
single-request smoke test using actual configured credentials. Follow
[OPENAI_SETUP.md](python-backend/OPENAI_SETUP.md), restart both API and worker,
and inspect `/model` in the terminal frontend.

The earlier live-provider, hosted CI billing and production-infrastructure gates
remain open unless separately verified. This follow-up does not authorize or
claim deployment/merge, nor complete all original production-readiness work.
