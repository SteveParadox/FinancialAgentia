# Research API v1

All endpoints use server authentication and principal ownership. JWT mode uses `Authorization: Bearer …` with configured HS256 secret/issuer/audience and production expiry requirement. Shared `X-API-Key` mode is a single shared account; it is intended for trusted server/terminal clients, not per-user browser identity. No built-in password account-registration/SSO issuance service is supplied. The browser consumes tokens from an operator-configured identity issuer.

| Method / path | Behavior |
| --- | --- |
| POST `/api/v1/agents/executions` | 202; question, optional UUID session and validated scope; `Idempotency-Key` (8–128 characters) required |
| GET `/api/v1/agents/executions` | Owner history, `limit=1..100`, opaque `cursor=<last execution UUID>`; legacy timestamp `before` retained |
| GET `/api/v1/agents/executions/{id}` | Committed status, question, stage, timestamps, attempts, model, scope and saved result |
| GET `/api/v1/agents/executions/{id}/events` | Authenticated SSE; Redis IDs, Last-Event-ID replay, heartbeats and terminal snapshot |
| GET `/api/v1/agents/executions/{id}/result` | Validated persisted report; 409 until a report exists |
| POST `/api/v1/agents/executions/{id}/cancel` | Idempotent owner cancellation |
| POST `/api/v1/agents/executions/{id}/retry` | 202 linked successor; failed/timed-out only; idempotency key required; single successor atomically |
| POST `/api/v1/agents/executions/cancel-by-key` | Race-safe cancellation before admission acknowledgement |
| GET `/api/v1/model` | Authenticated configured model/provider/configuration presence; never keys or asserted live connectivity |
| GET `/health`, `/ready` | Liveness; dependency/configuration/worker readiness (503 when unavailable) |

Example body:

```json
{"query":"Compare Microsoft and Apple profitability and financial health","scope":{"category":"comparison","symbols":["MSFT","AAPL"]}}
```

`category`: `company`, `comparison`, `historical_prices`, `general`. Historical price scope requires `start_date`, `end_date` in ISO date format and symbols; other categories reject dates to prevent unsupported point-in-time fundamental research. Company scope requires one symbol, comparison 2–5. Extra request fields, duplicate/invalid symbols and future/reversed dates fail validation.

Errors: 401 authentication, 404 unknown/unowned jobs, 409 idempotency/eligibility/result conflicts, 422 invalid input, 429 capacity/rate limit, 503 storage/tools unavailable. Redis connection errors return sanitized 503/Retry-After, never connection strings. Cancellation or unauthorized lookup never reveals another user's report. History excludes report bodies; page filters/search in the browser apply to loaded records. Exports are generated from an already-authorized saved report, not a second unsecured backend endpoint.
