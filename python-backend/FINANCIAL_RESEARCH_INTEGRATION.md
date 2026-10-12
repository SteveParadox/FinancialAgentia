# FinancialAgentia: financial research integration

This branch is **stacked on the Phase 1 backend repair PR**. Merge or rebase that dependency before integrating this work into main.

## Executable path

React Ink CLI -> versioned POST /api/v1/agents/execute -> Python FastAPI -> authenticated Python agent orchestrator -> ToolExecutor -> Financial Datasets adapter -> LLM evidence synthesis -> final-answer SSE -> CLI.

Python owns agent execution, the configured LLM, session persistence, and research tool invocation. TypeScript is the UI client. The existing TypeScript research tools remain for compatibility but are not the CLI's research path. The legacy /query endpoint is currently retained for external direct-LLM consumers, but the CLI does not call it; consolidate/remove that endpoint after migrating its existing consumers and tests.

## Local configuration and startup

From python-backend, install requirements, then configure:
- FINANCIAL_DATASETS_API_KEY: required to register production financial tools.
- OPENAI_API_KEY and LLM_MODEL, or credentials for the selected supported backend model.
- FINANCIAL_DATA_TIMEOUT_SECONDS: optional timeout, default 12, max 60.
- BACKEND_API_KEY or JWT_SECRET for authenticated operation.
- BACKEND_ENV=production and SECURE_COOKIES=true in production.

Launch backend: python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Launch CLI from repository root: bun run start
For remote backend, configure FINANCIALAGENTIA_BACKEND_URL and use HTTPS with credentials.

Local CLI session IDs are explicitly forwarded to Python to preserve backend history. Ctrl+C aborts the HTTP request. The Python agent emits one final SSE answer after research has finished, not token-by-token progress.

## Tools and provenance

The Python adapter uses the preexisting Financial Datasets endpoints for price snapshots, historical prices, company metric snapshots, historical financial metrics, annual/quarterly/TTM statements, SEC filings metadata and company news. A technical indicator tool computes SMA and simple RSI from actual historical closes.

Each financial result contains source_url, provider, ticker, retrieved_at, and data_timestamp if the provider supplied one. Results are not necessarily real-time. Empty responses, absent credentials, failed HTTP requests, and invalid parameters are errors, never invented fallback prices.

The planner receives actual Python tool names and callable signatures. Failed tools cannot be counted as completed research. No paid-provider live request is claimed in tests.

## Validation commands

Python compile and tests (from python-backend):
- python -m compileall -q app dexter_py tests
- python -m pytest -q

TypeScript (from repository root):
- bun install --frozen-lockfile
- bun run typecheck
- bun test

Operational acceptance requires a configured real provider, an actual CLI research request, citation/source verification, an invalid-symbol request, a missing-key boot, and a cancelled in-flight request. Integration here is not evidence of production reliability.

## Explicit remaining limitations

This implementation provides a source-backed adapter and the authoritative CLI-to-agent path, not every optional finance feature. Company profile enrichment, SEC filing full-text extraction, extensive balance-sheet calculations, financial-news classification, provider failover, multi-worker session locking, and true token-level progress streaming need separate implementation and validation. Provider output shapes and entitlement tiers require real-credential verification. Model selection from the old CLI is deliberately disabled as misleading; LLM_MODEL on the Python backend is authoritative. Existing legacy direct-LLM /query route remains available outside the CLI.

## Follow-up bug audit

The follow-up review tightened source validation and cross-runtime execution:
- Removed undefined TS AgentCallbacks usage from the active hook.
- Removed fabricated completed-phase indicators. CLI shows generic backend research progress until completion.
- Added incremental size-bounded SSE parsing and an overall client deadline.
- Added stale/cancelled execution-generation guards and response-render cancellation cleanup.
- Added Python tool signature preflight and prevented retry loops for invalid arguments or provider-final errors.
- Added strict ticker and positive-finite-price checks, malformed historical close detection, provider Retry-After handling, bounded financial data retrieval, and explicit partial-statement metadata.
- Added deterministic regression coverage for financial corruption, report-period mismatch, malformed SSE, and cancelled/stale UI races (where applicable).

**Remaining gates:** GitHub Actions jobs still report failure with empty step arrays and missing downloadable logs. Neither passing Python/Bun suites nor a real Financial Datasets call can be claimed. The current CLI cancellation guarantees local transport abort only; cancellation of Python work across disconnects/multiple ASGI workers still requires an operational test and explicit execution-state ownership. Legacy direct-LLM /query remains available outside the CLI. Treat the PR as draft until independently executed validations succeed.
