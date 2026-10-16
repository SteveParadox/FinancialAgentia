# Documentation validation report

**Source basis:** FinancialAgentia `main` inspected on 2026-10-16, beginning at commit `5101a15de18cf531536b464c6b3766f3f9375768` (merge of financial intelligence platform PR #11).

## Inspected source paths

- Root `README.md`, `package.json`, `env.example`, `docker-compose.research.yml`.
- `python-backend/.env.example`, `python-backend/Dockerfile`, backend README and `END_TO_END_OPERATIONS.md`.
- `python-backend/app/main.py`, `execution_api.py`, `execution_worker.py`, `workspace.py`, `platform_api.py`, `platform_store.py` and `monitoring.py`.
- `python-backend/dexter_py/tools/portfolio.py`, model connection script and platform tests.
- `web/main.tsx` and `web/platform.tsx`.
- Existing architecture, deployment, implementation and testing documents.

## Corrected inconsistencies

The previous root README described an older terminal-first system and recommended legacy commands such as starting the frontend with a standalone terminal entry point. The primary README now links the current browser-served, backend-worker architecture.

The base Docker Compose file is **production mode** and cannot provide a blank-token browser first run. A separate local-only overlay was added to allow private development startup without provisioning a JWT issuer. It overrides authentication *only in this explicit local mode*, without modifying the secure base Compose settings. The public-loopback rule and non-production warning are repeated at the point of use.

Feature documentation distinguishes implemented behaviors from aspirational scope: 2 explicit financial sources (one optional SEC configuration), saved scoped research context rather than universal vector memory, long-only single-currency ledger, periodic UTC in-app alerts rather than email/streaming, and principal-ID invitations rather than email invitations.

## Verified by repository inspection

- `bun run build:web` is declared in root `package.json`.
- API module `app.main:app` serves the browser workspace and exposes `/health`, `/ready` and versioned APIs.
- Separate worker entry point is `python -m app.execution_worker`.
- The checked-in Compose services are `api`, `worker` and `redis`.
- `python-backend/.env.example` describes current known variables.
- `platform_api.py` has portfolios, research context, financial data, monitoring, workspaces and export routes.
- Browser navigation and key actions correspond to `web/main.tsx` and `web/platform.tsx`.
- Portfolio ledger CSV columns, transaction types, limitations and risk proxy were read from the portfolio module.
- Source files and documentation targets in the new guides are present or added in this changeset.

## Not executed in this session

Network access from the working container could not resolve github.com for a Git clone. Source reading/writing was performed through the connected GitHub tool instead. Accordingly **no local installation, Python tests, Bun builds, Docker Compose run, browser test or paid-provider live test was executed** as part of this documentation change. Commands were checked against repository manifests and entry points, not advertised as operationally verified here.

Manual follow-up on a real Windows or Linux/macOS host is necessary before classifying the full startup path as executed successfully. See [verification checklist](testing/verification.md) and [production gates](deployment/production-guide.md).
