# Reproducing validation

Start disposable Redis (example DB 15) without affecting production namespaces. Tests allocate unique prefixes and delete only their own keys. From repository root with Python environment activated and Bun in PATH:

```bash
python -m pip check
PYTHONPATH=python-backend TEST_REDIS_URL=redis://127.0.0.1:6379/15 python -m pytest -q python-backend
ruff check python-backend
python -m compileall -q python-backend/app python-backend/dexter_py python-backend/tests
PYTHONPATH=python-backend python python-backend/scripts/export_contracts.py --check
bun install --frozen-lockfile
bun run typecheck
bun run typecheck:web
bun test
bun run build
bun run build:web
bunx playwright install --with-deps chromium
TEST_REDIS_URL=redis://127.0.0.1:6379/15 bun run test:web
bun audit
```

`PYTHON` can select the browser fixture server interpreter. `WEB_TEST_BASE_URL` uses an already-running test fixture server (never point this suite at production). `CHROMIUM_EXECUTABLE_PATH` optionally selects an operator-provided browser binary. The normal CI path installs Playwright Chromium. In the implementation environment the Playwright CDN returned HTML in place of its archive; a Chromium 143 binary supplied through a temporary npm tooling package was used locally. That package is not a product dependency. Browser traces/screenshots are emitted on failure and ignored by git.

The browser test server starts automatically, requires TEST_REDIS_URL and fails visibly if it cannot start. Tests cover company research, comparison, five-year price analysis, refresh, sources/calculations, JSON export, cancellation, provider outage/retry, stream/poll reconnect, mobile overflow, multi-user endpoints, invalid credentials and escaped request text. Markdown export and the native print/PDF flow share the authorized saved report; exact browser-generated PDF pagination is not independently certified.

Real Redis tests additionally cover concurrent identical admission, lease exclusivity/expiry, fencing, process-kill recovery, deadline/cancel races, queue fairness, exhaustion, checkpoint replay, retry single-successor races, legacy history migration, equal-score pagination, expiry cleanup, and temporary registry failure. Required integration tests skip with an explicit reason when Redis/Bun are absent; CI supplies both and must not count those skips as verification.

See `../implementation-report.md` for the exact final run counts, failed intermediate runs and external release gates.
