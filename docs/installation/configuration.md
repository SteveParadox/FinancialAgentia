# Configuration, authentication and providers

The **backend configuration file** is `python-backend/.env`, copied from `python-backend/.env.example`. Both the API and worker must use the same settings. The terminal-only Bun client has a different, optional root `env.example`.

Keep all LLM and financial-source secrets **server-side**. Do not place them in bundled web frontend variables.

## Minimal local example

For a local-only installation bound to `127.0.0.1`, configure:

```dotenv
BACKEND_ENV=development
SECURE_COOKIES=false
REDIS_URL=redis://127.0.0.1:6379/0
BACKEND_API_KEY=
JWT_SECRET=
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=YOUR_OWN_OPENAI_KEY
FINANCIAL_DATASETS_API_KEY=YOUR_OWN_LICENSED_KEY
```

Keep the other lines from the checked-in `.env.example`. Change `LLM_MODEL` to a model your account can actually use. The optional `docker-compose.local.yml` overlay overrides `REDIS_URL` inside containers to `redis://redis:6379/0` and intentionally turns off authentication. It must not be used publicly.

## Environment variable reference

Defaults below follow `python-backend/.env.example` and existing backend configuration. Empty means not configured. Consult current source before changing values after an upgrade.

| Name | Purpose / example | Required |
| --- | --- | --- |
| `REDIS_URL` | Redis URL; host manual default `redis://127.0.0.1:6379/0` | For durable jobs |
| `EXECUTION_REDIS_PREFIX` | Redis namespace; `financialagentia:` | No |
| `BACKEND_ENV` | `development` or `production` | Yes for secure deployment |
| `BACKEND_API_KEY` | Trusted one-principal API-key header | Conditional auth |
| `JWT_SECRET` | HMAC JWT signing/verification secret for multi-user bearer auth | Conditional auth |
| `JWT_ISSUER` | Optional expected JWT issuer | When issuer validation is used |
| `JWT_AUDIENCE` | Optional expected JWT audience | When audience validation is used |
| `SECURE_COOKIES` | `true` required in production | Production |
| `LLM_MODEL` | Selected model ID; template `gpt-4o-mini` | For AI research |
| `OPENAI_API_KEY` | OpenAI server credential | With OpenAI |
| `OLLAMA_BASE_URL` | Native Ollama endpoint; local template `http://127.0.0.1:11434` | With Ollama |
| `OLLAMA_API_KEY` | Ollama cloud credential | With cloud |
| `ANTHROPIC_API_KEY` | Optional legacy provider credential; compatible packages also required | Optional |
| `GOOGLE_API_KEY` | Optional legacy provider credential; compatible packages also required | Optional |
| `FINANCIAL_DATASETS_API_KEY` | Licensed market/fundamental/news provider credential | For Financial Datasets research |
| `FINANCIAL_DATA_TIMEOUT_SECONDS` | Financial provider HTTP timeout; template `12` | No |
| `SEC_USER_AGENT` | Honest organization/contact identity for SEC requests | With SEC |
| `SEC_COMPANIES` | Verified ticker-to-CIK mapping JSON; `{}` disables mappings | With SEC |
| `AGENT_MAX_ITERATIONS` | Planning/reflection iteration cap; template `3` | No |
| `AGENT_EXECUTION_DEADLINE_SECONDS` | Job deadline; template `300` | No |
| `RESEARCH_MAX_ACTIVE_PER_USER` | Concurrent user job cap; template `5` | No |
| `LLM_TIMEOUT_SECONDS` | Model transport timeout; template `60` | No |
| `LLM_MAX_RETRIES` | Model retry attempts; template `3` | No |
| `LLM_MAX_OUTPUT_TOKENS` | Per-response cap; template `4096` | No |
| `RESEARCH_MAX_MODEL_CALLS` | Whole-attempt LLM calls; template `24` | No |
| `RESEARCH_MAX_RESERVED_OUTPUT_TOKENS` | Output token reservation budget; template `98304` | No |
| `RESEARCH_MAX_MODEL_INPUT_CHARACTERS` | Input character budget; template `350000` | No |
| `RESEARCH_RETENTION_SECONDS` | Terminal research artifact TTL; template `86400` (24 hours) | No |
| `PLATFORM_RETENTION_SECONDS` | Temporary context, invitation and notification TTL; template `7776000` (90 days) | No |
| `MARKET_SNAPSHOT_MAX_AGE_SECONDS` | Age-label policy, template `345600` | No |
| `FINANCIAL_STATEMENT_MAX_AGE_DAYS` | Age-label policy, template `550` | No |
| `CORS_ORIGINS` | Explicit allowed browser origins; never `*` | When cross-origin |
| `WEB_DIST_DIR` | Override compiled browser asset directory (default checkout `dist/web`) | Optional |

The reference configuration also describes the opt-in `RUN_LIVE_SEC=1` test toggle. This is a **test-run flag**, not normal production activation.

Portfolio, monitoring and workspace operations currently use the authenticated platform API and Redis. No separate email-sending configuration or SQL database is required by the checked-in platform. Don't invent `DATABASE_URL`, broker-specific or SMTP variables for this implementation.

## AI models

### OpenAI

Set both `LLM_MODEL=<available chat-completions-compatible model>` and `OPENAI_API_KEY=<your own secret>`. The existing template uses `gpt-4o-mini`; account/model entitlement controls whether calls succeed. The alternative model ID syntax `openai/<model-id>` explicitly selects OpenAI.

No Ollama daemon or Ollama key is needed when using OpenAI. Restart **both** backend API and worker after changing model configuration. A queued job created with a different model configuration is rejected rather than silently switched.

From `python-backend` with the virtual environment interpreter:

```bash
../.venv/bin/python scripts/check_model_connection.py
../.venv/bin/python scripts/check_model_connection.py --live
```

The first command checks configuration **only**. The second executes a real provider request and **may be billed**. Windows: `..\.venv\Scripts\python.exe scripts\check_model_connection.py` (optionally `--live`).

### Ollama local

Install and run Ollama, pull a suitable model, and confirm `ollama list` shows the installed ID. Configure:

```dotenv
LLM_MODEL=ollama-YOUR_INSTALLED_MODEL_ID
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_API_KEY=
```

Use an actual installed model that satisfies the backend's model capabilities. Check connectivity with `ollama list` and the model connection script above. In containers, `127.0.0.1` is the container itself; you must configure an explicitly reachable and appropriately secured host/container endpoint. Do not expose Ollama unauthenticated to the internet.

### Ollama cloud

If the account has access, configure `LLM_MODEL=ollama-cloud/<available API model ID>`, `OLLAMA_BASE_URL=https://ollama.com`, and `OLLAMA_API_KEY`. Credentials are sent to the expected Ollama HTTPS origin. Do not assume local and cloud model IDs or entitlements are interchangeable.

### Provider readiness

`GET /api/v1/model` (authenticated) reports configured model selection, not a successful paid test. `GET /ready` likewise checks configuration and worker availability, not guaranteed provider entitlement. No automatic cross-provider fallback is advertised.

## Financial providers

| Provider | Current supported scope | Configuration |
| --- | --- | --- |
| Financial Datasets | Quotes, historical prices, company financial statements and news via registered tools | `FINANCIAL_DATASETS_API_KEY` |
| SEC EDGAR | Configured original filing financial facts for known issuer mappings | `SEC_USER_AGENT` and `SEC_COMPANIES` |

For SEC, use verified CIK IDs, exchange identifiers and a real organization/contact user agent. Map only supported issuers, for example the **shape** `{"SYMBOL":{"cik":"10-digit-id","exchange":"venue","name":"issuer"}}`. Replace placeholders with actual verified issuer details. The provider does not guess identities from ticker alone.

The browser's **Financial data** screen offers quote, annual statements, historical prices (date range required), and SEC facts. `GET /api/v1/platform/providers` shows configured sources with `live_verified:false` by default. That field is not an indication of a live paid connectivity test. There is no silent fallthrough from missing live quotes to historic filings.

Freshness labels describe observation age and evidence quality; they do **not** establish a live-market entitlement or guaranteed latest published filing.

## Authentication and browser access

**Development (private loopback only):** both `JWT_SECRET` and `BACKEND_API_KEY` can be blank. Open the browser and leave **Access token** blank. The application then behaves as one shared local principal. Never bind this unsecured configuration to a public interface.

**Multiple users:** configure a strong `JWT_SECRET` and, as applicable, `JWT_ISSUER` and `JWT_AUDIENCE`. Provision/issue compatible HS256 bearer JWTs with stable `sub` (or `user_id`); production JWTs require `exp`. The application verifies tokens but **does not issue user accounts or provide a web signup page**. Paste the issued JWT into the browser connection screen. The browser retains credentials in memory, so a full reload requires reconnecting.

**Trusted API/terminal clients:** `BACKEND_API_KEY` can authenticate via `X-API-Key` when JWT auth is not enabled. This represents one shared principal, **not** multiple private accounts. The browser **does not accept the shared API key as its token**. Do not paste a shared backend key into the browser or ship it to JavaScript clients.

Production requires `BACKEND_ENV=production`, `SECURE_COOKIES=true` and configured JWT or shared trusted API key, together with HTTPS and properly protected Redis. The base Docker Compose sets production mode; see [production deployment](../deployment/production-guide.md).

To inspect the authorized principal, call `GET /api/v1/platform/me` using the authentication method of the caller. Collaboration invitations target the returned 64-character account/principal identifier, **not an email address**.

## Optional terminal client

The legacy Bun/Ink client uses the root `env.example` values `FINANCIALAGENTIA_BACKEND_URL`, `BACKEND_API_KEY` or `AUTH_BEARER`. It must point at the running FastAPI/worker stack. From repository root: `bun start`. Keep AI/data provider secrets in the backend env only.
