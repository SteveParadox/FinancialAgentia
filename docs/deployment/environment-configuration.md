# Local startup and configuration

Use Python 3.11+ and Bun 1.4.3+. From repository root:

```bash
bun install --frozen-lockfile
bun run build:web
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip==26.2.1
.venv/bin/python -m pip install -r python-backend/requirements-dev.txt
cp python-backend/.env.example python-backend/.env
```

Configure a persistence-enabled Redis, authentication and licensed Financial Datasets access in the backend `.env`. Then run two processes from `python-backend`:

```bash
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
../.venv/bin/python -m app.execution_worker
```

Open `http://127.0.0.1:8000/`. Serve the browser and API on the same HTTPS origin in production. The root returns a clear 503 until assets are built. `WEB_DIST_DIR` overrides the default checkout `dist/web`. Authentication is enforced on API data; the public static shell contains no secrets. Only local loopback HTTP may hold a browser token. Browser credentials stay in memory; full refresh requires reconnection but retains the saved job URL. Never prefix model/provider keys with a frontend environment variable.

## Models

OpenAI: set `LLM_MODEL=<chat-completions-compatible model ID>` or `openai/<model-id>` and `OPENAI_API_KEY`. Account entitlement controls availability; no model is guaranteed available. Official AsyncOpenAI uses a fixed OpenAI endpoint, max_completion_tokens, no SDK retry multiplication, bounded shared retries/deadlines and validated streaming. Reasoning families omit unsupported temperature options.

Ollama local: set `LLM_MODEL=ollama-<installed-model-id>`, `OLLAMA_BASE_URL=http://127.0.0.1:11434`; no API key is needed. The native HTTP client maps output caps to `options.num_predict`, verifies terminal NDJSON, ignores unrelated global proxies for local requests and rejects redirects. The old LangChain Ollama adapter was replaced because its pinned version ignored these options.

Ollama cloud: `LLM_MODEL=ollama-cloud/<API model ID>`, `OLLAMA_BASE_URL=https://ollama.com`, `OLLAMA_API_KEY`. Use an ID actually available to the account; legacy `ollama-…-cloud` names remain routed without silently rewriting the model. Cloud credentials are restricted to the Ollama HTTPS origin. Existing optional Anthropic/Google routing remains; their optional adapters require compatible separately installed packages and were not live tested.

API and worker must use identical model configuration. `/api/v1/model` reports the server selection. A queued job whose model no longer matches fails explicitly. Run `python scripts/check_model_connection.py` for configuration only; `--live` makes one real model call which may incur cost. No silent provider/model fallback is enabled.

## Limits

See `.env.example` for all names/defaults. `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`, `LLM_MAX_OUTPUT_TOKENS` control transport. `RESEARCH_MAX_MODEL_CALLS`, `RESEARCH_MAX_RESERVED_OUTPUT_TOKENS`, `RESEARCH_MAX_MODEL_INPUT_CHARACTERS` bound each attempt including retries/JSON repair. `AGENT_MAX_ITERATIONS`, `AGENT_EXECUTION_DEADLINE_SECONDS`, `RESEARCH_MAX_ACTIVE_PER_USER`, `RESEARCH_RETENTION_SECONDS` bound lifecycle/resources. `MARKET_SNAPSHOT_MAX_AGE_SECONDS` and `FINANCIAL_STATEMENT_MAX_AGE_DAYS` label observation age. Invalid bounded settings fail startup. Defaults are conservative policy, not market-data accuracy or billed-cost guarantees.

Production additionally requires `BACKEND_ENV=production`, strong `JWT_SECRET` or trusted shared `BACKEND_API_KEY`, `SECURE_COOKIES=true`, explicit CORS origins, Redis URL/TLS/ACL/backups and licensed provider credentials. JWT issuer/audience should match your issuer. Docker Compose reads backend `.env`, not browser environment variables.
