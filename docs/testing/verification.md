# Tests and documentation verification

The commands below correspond to checked-in scripts and source tests on repository `main` as inspected for this documentation change. **They have not been executed in this documentation-only connector environment.** Run them on a machine with Python, Bun and the required Redis fixture/runtime.

## Install

From repository root, with Python 3.11+ / Bun 1.4.3+:

```bash
bun install --frozen-lockfile
python3 -m venv .venv
.venv/bin/python -m pip install -r python-backend/requirements-dev.txt
```

On Windows PowerShell replace `python3` with `py -3` (or a verified `python`) and `.venv/bin/python` with `.\.venv\Scripts\python.exe`.

## Frontend

From repository root:

```bash
bun run typecheck
bun run typecheck:web
bun test
bun run build
bun run build:web
```

Browser E2E is `bun run test:web` (Playwright). It requires installed browser binaries plus configured test Redis/runtime fixtures. See `playwright.config.ts` and `tests/browser/`; a successful fixture-based test does **not** prove external paid-provider access.

## Python

From repository root, with the virtualenv active and a disposable Redis test database reachable:

```bash
.venv/bin/python -m ruff check python-backend
.venv/bin/python -m compileall -q python-backend/app python-backend/dexter_py python-backend/tests
PYTHONPATH=python-backend .venv/bin/python python-backend/scripts/export_contracts.py --check
PYTHONPATH=python-backend TEST_REDIS_URL=redis://127.0.0.1:6379/15 .venv/bin/python -m pytest -q python-backend
```

Use a **dedicated disposable** Redis database and verify the chosen test URL is not production. PowerShell syntax differs:

```powershell
$env:PYTHONPATH = "python-backend"
$env:TEST_REDIS_URL = "redis://127.0.0.1:6379/15"
.\.venv\Scripts\python.exe -m pytest -q python-backend
Remove-Item Env:TEST_REDIS_URL
Remove-Item Env:PYTHONPATH
```

Be aware that the repository's installed development requirements contain Ruff; `pytest` and fixtures are part of the checked-in test setup. If an installer or dependency import fails, report and repair it rather than saying tests passed.

## Docker and runtime checks

From repository root:

```bash
docker compose -f docker-compose.research.yml -f docker-compose.local.yml config
docker compose -f docker-compose.research.yml -f docker-compose.local.yml up -d --build
docker compose -f docker-compose.research.yml -f docker-compose.local.yml ps
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/ready
```

A healthy `/ready` only checks model configuration, not a paid live inference or live quote. For an *opt-in* live LLM test, from `python-backend` invoke `python scripts/check_model_connection.py --live` using the configured interpreter (billed provider usage possible).

Existing provider tests include `python-backend/tests/test_live_provider.py` and `test_sec_live.py`, whose live cases require explicit credentials, permissions and flags. Do not silently treat skipped live tests as verified.

## Feature tests in source

`python-backend/tests/test_platform.py` includes fixture-based scenarios for portfolio ledger, risk, memories, invitations/permissions, collaborative publications, alerts, scheduling, failure/recovery and provider normalization. `tests/browser/platform.e2e.ts` covers browser workflows. Test expectations depend on controlled input fixtures and must not be advertised as independent verification of market data.

## Documentation acceptance checklist

- [x] Commands and paths inspected in main-branch repository sources.
- [x] Browser, API, worker and Compose roles reconciled with source.
- [x] Root README updated to point to the current browser workflow.
- [x] Windows PowerShell and POSIX shell commands separated.
- [x] Local-only no-auth Docker override explicitly segregated from production.
- [ ] Full dependency installation, Docker startup and live browser research executed on a capable host.
- [ ] Fresh Windows runtime smoke test executed.
- [ ] External OpenAI/Ollama and licensed Financial Datasets live tests executed.
- [ ] Deployment-specific production release gates verified.

**Do not convert pending checks to passed without actual execution results.** See [documentation validation report](../documentation-validation.md).
