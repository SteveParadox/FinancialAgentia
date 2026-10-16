# Groq provider configuration

FinancialAgentia supports Groq with its existing OpenAI-compatible chat-completions
transport. Groq is an **explicit provider**, not a fallback from OpenAI.

## Backend setup

In `python-backend/.env` (not the client root `.env`):

```dotenv
LLM_MODEL=groq/openai/gpt-oss-120b
GROQ_API_KEY=YOUR_REAL_GROQ_API_KEY
GROQ_BASE_URL=https://api.groq.com/openai/v1
OPENAI_API_KEY=
```

`groq/` chooses the Groq provider. The remainder,
`openai/gpt-oss-120b`, is Groq's model ID and **must not** be stripped.
Set `GROQ_API_KEY` to the credential issued by your Groq account. Do not
commit it, put it in the browser bundle, or reuse `BACKEND_API_KEY`.

`GROQ_BASE_URL` is read by the backend when selecting a Groq model.
For credential safety only the official
`https://api.groq.com/openai/v1` URL (optionally with a trailing slash)
is supported. Non-Groq hosts, HTTP, custom paths, query strings and
embedded credentials are rejected. The setting does **not** affect
`openai/<model-id>`, which continues to use OpenAI's endpoint and key.

These settings affect both the API and the durable Python research worker,
which load the same backend `.env`. Restart **both** processes after edits.
Keep your existing `REDIS_URL` and `EXECUTION_REDIS_PREFIX` unchanged.

## Verify on Windows CMD

Run from `FinancialAgentia\python-backend`:

```bat
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python scripts\check_model_connection.py
python scripts\check_model_connection.py --live
```

The first model check verifies configuration and **does not** perform a
network request. `--live` issues a real Groq API call which might incur
usage charges and depends on model entitlements and a valid key.

For the manual runtime run FastAPI and `python -m app.execution_worker`
in separate terminals with Redis already running. Port 18080 may be used
for FastAPI when 8000 is unavailable; update
`FINANCIALAGENTIA_BACKEND_URL` for any separate Bun terminal client.

See [canonical configuration and auth](../installation/configuration.md)
for other provider credentials and Redis/deployment considerations.
