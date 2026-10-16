# FinancialAgentia implementation report — 2026-10-16

## A. Initial problems and baseline

Inspected default branch `main` at `6a5139a39b07a55dd7e64b037ededc7d786d53ea`, recent commits, all remote branches and outstanding PRs. PR #9 had been merged; no open PR existed. Older implementation branches introduced no unmerged commits beyond this base. The clean working tree was preserved and work moved to `codex/web-research-workspace`.

The application was a Bun/React Ink terminal client with FastAPI, Redis Lua/Streams, a standalone Python worker and custom Python reasoning orchestrator. Prior work already provided fencing, owner-scoped admission, replay and an official OpenAI SDK integration. These were retained rather than claimed as new work.

Confirmed missing/broken paths: no browser workspace; no user retry/report-only APIs; checkpoints saved but not restored; temporary Redis failures could stop polling; an uncertain heartbeat could become a false user cancellation; history omitted questions and could stop at expired/equal-time entries; missing configurable observation freshness; wrong Sharpe units; timestamp inheritance across fiscal records; incomplete derived input lineage; sparse histories treated as daily returns; the pinned Ollama adapter ignored cloud-header/output-limit arguments. Dependency audits also identified inherited vulnerable packages.

Baseline: **128 Python passed, 12 opt-in live tests skipped** using actual Redis and Bun. Full discovery and the execution map are in [current-state-audit.md](architecture/current-state-audit.md).

## B. Architecture changes

Retained one authoritative **Python Orchestrator**. TypeScript `Agent` remains a durable backend client. No LangGraph or competing workflow service was introduced. Job admission, queueing, owner authorization, attempts, lease/fencing, deadlines, cancellation, terminal writes, events and history remain exclusively in the Redis registry/worker layer. Reasoning owns validated understanding, DAG plans, narrow specialist tasks, reflection and synthesis.

Added a React DOM browser client using the existing React/Bun stack; Ink cannot render browser controls. FastAPI serves the compiled assets on the API origin, avoiding a new deployed service. The browser has no financial tools, provider keys or independent orchestration.

## C. Backend implementation

- Atomic, authorized single-successor retry; replayed same keys reuse the child, concurrent different tokens conflict, source attempts are preserved.
- Authenticated report retrieval and typed research scope, including instrument/date validation and payload-bound idempotency.
- Completed-plan checkpoint restoration, schema/model compatibility checks and reused original tool evidence. Interrupted uncommitted plans may repeat read-only calls; no exactly-once billing claim is made.
- Redis polling/heartbeat recovery, sanitized storage 503s, and a regression preventing storage uncertainty from being finalized as user cancellation.
- Owner history includes questions and retry links; ID cursor handles equal timestamps; expired hashes are removed lazily so history does not disappear behind empty pages.
- Configurable terminal retention (default 24 hours, bounded 1 hour–1 year), model-call/output reservations/input-character budgets, startup validation and model/workflow/attempt metadata.
- Existing cancellation, generation fences, deadlines, maximum claim attempts, session serialization, capacity limits and transactional admission/result commits remain enforced.

Main affected modules: `app/execution_api.py`, `app/execution_worker.py`, `app/main.py`, `app/workspace.py`, `dexter_py/agent/execution_registry.py`, `orchestrator.py`, `financial_evidence.py`, `model/budget.py`.

## D. Financial accuracy

Added explicit historical/stale/unverified/reported-period labels and configurable age policies. No quote is called live merely because the cache is fresh. Fiscal records retain their own reporting dates; filing dates, original fields and filing references are preserved when supplied. Provider paths follow current official canonical endpoint documentation; documented operating cash flow is mapped to its canonical metric.

Sharpe is dimensionless. Sparse intervals above four calendar days omit daily volatility/Sharpe instead of producing misleading annualization. This heuristic is not a verified exchange calendar. Added deterministic gross/operating margins, current ratio, debt/equity and interest coverage when compatible source inputs exist, in addition to existing return/risk/margin calculations.

Report validation rejects duplicate/unresolved identities, dangling calculation inputs and inconsistent claims. A calculated aggregate cannot be certified when input evidence was truncated or rejected. Historical price mode filters dates/instruments and excludes current snapshots/fundamentals; point-in-time fundamental backtests remain unsupported. Conflicting same-context figures force partial classification and remain visible. Unvalidated model prose never enters the authoritative report.

Single-source provenance is not independent cross-source verification. Corporate-action policy, complete filing availability, restatements, exchange identity/calendar and licensed live freshness remain external/unverified limitations. No unsupported DCF, forward P/E, beta, MACD, ATR or qualitative investment thesis is claimed implemented.

## E. AI provider integration

OpenAI remains direct official AsyncOpenAI, with model IDs configured server-side, proper completion-token caps, bounded shared retries/deadlines, structured validation, refusal/truncation checks and cancellation-safe streaming. No Ollama service is needed for OpenAI.

Ollama now uses a bounded native `/api/chat` HTTPX transport because the pinned legacy LangChain adapter could not enforce the required options. It maps output caps to `options.num_predict`, validates terminal NDJSON, bounds buffers, normalizes provider errors, handles local proxies safely and restricts cloud credentials to the Ollama HTTPS origin. Explicit `ollama-cloud/<API model ID>` is supported alongside local and legacy routing.

Both providers have transport tests and complete durable research pipeline tests using genuine adapters with fixture upstream responses. Neither live OpenAI nor a running Ollama model was available. `check_model_connection.py` returned `configured:false, live_verified:false`; no paid call was performed. Optional Anthropic/Google routing is retained but requires compatible optional packages and was not live validated. Role-specific model selection, measured per-tenant billing and automatic provider fallback are not advertised.

## F. Frontend implementation

The responsive workspace provides token connection, research creation, company/comparison/historical-price scopes, server model information, meaningful stage timeline, authenticated streaming and polling fallback, cancellation, explicit retry, history/search/filters/pagination, saved reports, side-by-side company observations, historical close charts, evidence inspection, original filing links, freshness/coverage warnings and execution metadata.

JSON/Markdown exports and print/save-PDF use the authorized saved report. They do not create a second unauthenticated export endpoint. Search/filter/sort apply to loaded server history pages. Financial observations are paged to keep long histories usable. No fabricated chart data or unsupported research category is shown.

Tokens remain in memory. Refresh requires re-authentication and restores the job from its URL/backend persistence. Actual user account issuance/SSO is an operator integration, not a built-in registration/password system. Shared API key mode represents one account and is intended for trusted server/terminal clients. Browser rendering escapes untrusted text, rejects unsafe link schemes and uses a restrictive CSP. Remote browser access requires HTTPS.

## G. Database changes and compatibility

No SQL database or ORM was present; adding one would duplicate the authoritative Redis lifecycle. New fields/scope/retry links/metadata are additive. Report version 1 accepts old records through defaulted optional fields. Existing history is not dropped or overwritten. A representative old failed job without new fields is readable and safely retryable. Existing additive owner-index migration tests remain enabled. Changing retention does not retroactively extend an already-terminal record's TTL.

Recovery is at completed-plan boundaries, not at every in-flight HTTP byte. Redis primary durability, AOF/backup/restore and correct deployment remain required. Redis Cluster multi-slot execution is not supported by these Lua scripts.

## H. Actual verification

Local runtime: Python **3.12.14**, Bun **1.4.3**, Redis **7.0.15**, Chromium **143.0.7499.0** via Playwright **1.58.2**. CI targets Python 3.11/Redis 7.4, which are not substituted for local evidence.

| Check | Actual outcome |
| --- | --- |
| Clean virtualenv install of `requirements-dev.txt`; `python -m pip check` | Passed; no broken requirements |
| `PYTHONPATH=python-backend TEST_REDIS_URL=redis://127.0.0.1:6389/15 python -m pytest -q python-backend` | **153 passed, 12 skipped**, 289 existing/compatibility deprecation warnings; 9.05 s final parallel run |
| `ruff check python-backend` | Passed configured syntax/undefined/control-flow rule set; not a claim of full style or mypy certification |
| `python -m compileall -q python-backend/app python-backend/dexter_py python-backend/tests` | Passed |
| `PYTHONPATH=python-backend python python-backend/scripts/export_contracts.py --check` | Passed |
| `bun install --frozen-lockfile` | Passed |
| `bun run typecheck`, `bun run typecheck:web` | Passed |
| `bun test` | **16 passed, 0 failed**, 35 expectations |
| `bun run build` | Passed; terminal bundle 23 modules, approximately 62 KB with packages external |
| `bun run build:web` | Passed; HTML/JS/CSS emitted and served |
| `TEST_REDIS_URL=… PYTHON=<clean venv> CHROMIUM_EXECUTABLE_PATH=<local binary> bun run test:web` | **8 passed**; real HTTP/API/Redis/worker/model adapter/financial adapter/browser, fixture upstream HTTP only |
| `bun audit` | No known vulnerabilities; 79 dependency packages checked |
| `pip-audit --path <clean virtualenv site-packages>` | No known vulnerabilities, including installer updated to pip 26.2.1 |
| Clean Uvicorn startup and HTTP checks | `/health` 200; `/` and referenced assets 200; configured-model endpoint 200 with configured=false; `/ready` correctly 503 without required provider/worker readiness |
| Compose/workflow YAML parse | Passed structural parsing; not Docker execution |
| Diff whitespace / credential-pattern review | Passed; no real credential patterns found |
| Docker image/Compose execution | **Not executed: Docker unavailable** |
| Live OpenAI, Ollama and licensed financial-provider journeys | **Not executed: credentials/runtime/entitlements unavailable** |

Browser journeys: Microsoft company analysis with Decimal ratios, refresh and exact JSON export; Microsoft/Apple/Alphabet comparison with separate currencies/dates; five-year NVIDIA price observations and independently recomputed return with full input lineage; cancellation; provider outage and linked retry; SSE interruption/offline/mobile recovery; owner isolation on status/result/stream/cancel/retry/history; invalid credentials and escaped injection-like request text. These are synthetic upstream fixtures, not claims about those companies' current numbers.

Failure/recovery tests also cover real process-kill lease recovery, duplicate admission/claims, stale-owner fencing, cancellation/finish races, exhaustion, history migration/cursors, checkpoint reuse, partial/missing data, invalid model outputs, malformed provider records, missing lineage, freshness/conflicts and temporary Redis failures. No assertions were weakened or tests skipped to produce a passing run.

Intermediate defects were reproduced and corrected: hashed HTML initially lacked the stable index filename; browser fixture startup lacked PYTHONPATH; a local single-process Chromium option prevented context reuse; an obsolete duplicate test filename caused Bun to interpret Playwright tests; adding the comparison table required a precise accessible table locator; an interrupted environment left a truncated temporary Chromium executable, which was re-extracted. Earlier failed runs are not represented as successful final evidence.

## I. Security findings and remaining risks

Resolved: authenticated retry/report access; owner-safe history; no client provider keys; unsafe URLs/HTML escaped or rejected; bounded model requests, prompt/output/response sizes and job capacity; permanent provider errors not retried indefinitely; uncertain heartbeat/cancel race; complete input lineage; vulnerable inherited dependencies.

Initial scans reported 89 JavaScript advisories and 68 Python advisories across inherited versions. Compatible patches, removal of unused Jest/community/full-LangChain packages, modern typed-message dependencies, FastAPI/Starlette/AnyIO/HTTPX updates and migration from python-jose/ecdsa to PyJWT eliminated known findings in the scanned final environments. This is not a guarantee against unknown vulnerabilities or a substitute for base-image scanning and live configuration review. All Bun/transport/auth tests remain enabled.

Open risks: production JWT issuer integration, TLS/CORS, Redis ACL/persistence/backup recovery, actual provider licensing, network behavior and limits, load/soak testing, aggregate billing controls, long-term archival, and independent market/filing verification. Provider terms must permit the intended users, analysis retention and downloads; no entitlement was invented.

## J. Deployment readiness and remaining work

**Do not declare production readiness.** Connected deterministic journeys, clean startup/builds and concurrency/recovery contracts are verified locally. These mandatory external gates remain:

- **P0:** Successful required hosted CI on the published commit; validate actual configured OpenAI/Ollama and licensed financial-source execution; verify original financial records/adjustment/filing coverage; validate container deployment, production JWT/TLS, Redis persistence/restore and intended commercial/export rights.
- **P1:** Richer evidence-reviewed qualitative synthesis and coverage requirements; point-in-time filing/restatement/corporate-action support before historical fundamental backtests; independent licensed-source reconciliation; robust exchange calendar/identity resolution; aggregate tenant cost budgets, extended archives and production load/soak testing. No guarantee is made for the full historical fundamentals/valuation journey.
- **P2:** Expanded distributed traces/metrics and operator dashboards, optional provider compatibility matrix, keyboard/screen-reader audits beyond current accessible-control/browser checks, independent PDF pagination validation and remaining deprecation cleanups.

The Dockerfile/Compose now build browser assets from the repository root, run API and dedicated worker separately and preserve Redis AOF. They are reviewable but container execution is unverified. Setup and recovery commands are in [environment-configuration.md](deployment/environment-configuration.md), [worker-deployment.md](deployment/worker-deployment.md), and [recovery-procedures.md](deployment/recovery-procedures.md).

## K. Git delivery

Branch: `codex/web-research-workspace`; base: `6a5139a39b07a55dd7e64b037ededc7d786d53ea`.

Backend commit: `f8a4cf857545dabbdae878c1c1359c998998b642` — checkpoint/retry/recovery, financial/model correctness and Python dependency security.

Workspace/deployment commit: `c9535d60ff3c44226899c28ccfcd6d2c6555acee` — connected web workspace, exports, browser tests, build/deployment and JavaScript dependency hardening.

Draft PR: https://github.com/SteveParadox/FinancialAgentia/pull/10. No default-branch rewrite, force-push or merge was performed. The report/documentation commit follows these code commits; consult the PR commit list for its own identity.

### Hosted CI blocker verified on the published code

The two workflows on code commit `c9535d60ff3c44226899c28ccfcd6d2c6555acee` concluded failure **before executing jobs**:

- https://github.com/SteveParadox/FinancialAgentia/actions/runs/38876876815
- https://github.com/SteveParadox/FinancialAgentia/actions/runs/38876876836

Check `116893932628` annotation: **“The job was not started because your account is locked due to a billing issue.”** This is a hosted execution blocker, not passing CI and not a test failure that was suppressed. Restore account execution, then rerun required checks. A documentation-only follow-up may not trigger path-filtered workflows; the code commit's blocked runs still need to succeed.

The final browser rerun against the published, formatted code passed **8 tests in 16.0 s**. See [validation-output.txt](testing/validation-output.txt) for recorded output excerpts.
