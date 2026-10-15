# Connect FinancialAgentia to OpenAI

The Python research worker connects directly to the official OpenAI API through
the installed OpenAI SDK. Ollama remains an optional alternative; it is not a
proxy or prerequisite for OpenAI.

## Configure the backend

Install dependencies from this directory:

```sh
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` locally; never commit the real key:

```dotenv
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=<your actual OpenAI API key>
REDIS_URL=redis://127.0.0.1:6379/0
BACKEND_API_KEY=<your backend authentication secret>
FINANCIAL_DATASETS_API_KEY=<your financial data provider key>
```

`LLM_MODEL` accepts OpenAI chat model IDs, including the tested request shapes for
GPT-4o/GPT-4.1, GPT-5 family and o-series reasoning models. Use a model available
to your API project and compatible with Chat Completions. This is not a claim that
every model or fine-tune is available to the account. `openai/<model-id>` explicitly
selects OpenAI for custom/fine-tuned IDs without relying on a naming heuristic.
For example, `openai/ft:gpt-4o-mini:org:name:id` is a shape example, not a real model.

Restart both processes with the same environment after changing the model:

```sh
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# Separate terminal:
python -m app.execution_worker
```

With Compose, the existing shared env_file supplies both API and worker. Jobs
already admitted under another model fail with MODEL_CONFIGURATION_CHANGED
rather than silently running with a different provider/model. Submit new jobs
after the switch; existing reports remain retrievable within retention.

The terminal client needs only its backend URL and backend authentication:

```dotenv
FINANCIALAGENTIA_BACKEND_URL=http://127.0.0.1:8000
BACKEND_API_KEY=<the matching backend authentication secret>
```

Use AUTH_BEARER instead when the backend uses JWT authentication. Do not copy the
OpenAI key or financial provider key into a browser or public client bundle.

## Check the connection

From `python-backend`:

```sh
python scripts/check_model_connection.py
python scripts/check_model_connection.py --live
```

The first command reports model/provider/configuration presence without making a
network request. The second sends a single short model request and reports
live_verified only after a nonempty, normally completed response. API usage may
be billed. Keys, provider error bodies and generated text are not printed.

In the terminal app, `/model` queries authenticated `GET /api/v1/model`; it does
not display a stale local model selection. configured=true means the key/SDK
configuration exists; it does not prove entitlement or remote connectivity.

## Request handling

OpenAI requests use `max_completion_tokens`. Sampling temperature is omitted for
reasoning families, and trusted system messages map to developer messages there.
Classic chat models retain explicit temperature. One shared retry policy handles
transient connection, rate-limit and server failures; SDK retries are disabled to
avoid multiplying calls. Authentication failures are not retried.

Structured model outputs are still schema validated. Refusals, truncated
completions and streams missing their terminal marker fail instead of being
accepted as complete research. HTTP resources close during worker/API shutdown.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| configured=false | Set the key in the backend environment and restart API/worker; check LLM_MODEL spelling. |
| HTTP 401 | Correct the OpenAI API key. Repeated retries will not fix authentication. |
| HTTP 403/404 | Check project permissions and access to the exact model ID. |
| HTTP 429 | Check provider limits/quota; bounded retries cannot override account restrictions. |
| MODEL_CONFIGURATION_CHANGED | API and worker must use the same model; submit a new job after restarting both. |
| OpenAI smoke works, research fails | Check financial provider credentials, worker heartbeat and the job failure code. |

The review validated the official SDK's HTTP/SSE wire against fixtures and the
complete durable research path with simulated external model/provider responses.
No live OpenAI key was available in the implementation environment. Live access
must be checked with the actual server credentials; fixture tests are not live
connection verification.

Official API reference reviewed:
https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create
