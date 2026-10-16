# Troubleshooting FinancialAgentia

Use the actual result of `/ready` before assuming every failure is a frontend bug. The API, Redis and worker are separate dependencies.

| Symptom | Diagnostic action | Likely resolution |
| --- | --- | --- |
| Browser root returns 503 | Check `bun run build:web` and `dist/web`, or Docker API logs | Rebuild browser assets and restart API |
| `/health` unreachable | Check `docker compose ... ps`, logs and port 8000 | Start API, fix bind/port configuration |
| `/health` 200, `/ready` 503 | Inspect `/ready` JSON fields, worker logs and Redis | Configure model; start worker and Redis; allow heartbeat |
| Jobs stay `QUEUED` | Worker logs, Redis reachability, mismatched model | Start correct worker with matching model config |
| Research `FAILED` or `TIMED_OUT` | Job status and source limitations | Fix model/data keys; confirm data coverage; retry only when allowed |
| Browser says token rejected | Check `JWT_SECRET`, `sub`, issuer, audience and expiry | Obtain valid JWT from configured issuer; reconnect |
| Browser blank token doesn't connect | Check auth mode | Only local no-auth setup permits blank token; base Compose is production mode |
| Shared API key fails in browser | Browser uses `Authorization: Bearer`, not `X-API-Key` | Use provisioned JWT for browser; shared key for trusted API/terminal clients only |
| OpenAI 401/404 | Run model check without `--live`, then optional billed `--live` | Correct server-side key/model entitlement; restart API and worker |
| Ollama connection refused | Test installed model and reachable `OLLAMA_BASE_URL` | Start daemon; fix container/host networking |
| Missing quote/financial data | `GET /api/v1/platform/providers`, provider credentials, source warnings | Supply licensed source access; choose supported dataset/instrument |
| Data appears stale or partial | Inspect retrieved/observation timestamps and source info | Check market hours, delay policy and coverage; do not relabel historic data as live |
| Portfolio valuation unavailable | Check cash ledger, symbol, currency and quote availability | Use single-currency supported holdings and actual available quotes |
| CSV rejected | Check exact 9-column header and transaction rules | Preview import; deposit cash before buying; unique transaction IDs |
| Risk proxy unavailable | Check 1–10 holdings, daily series and currency | Use aligned dates with at least 31 daily observations; no gap filling |
| Memory not found | Check ownership, saved report success and expiry | Re-save eligible report; verify principal |
| Monitoring does not trigger | Check enabled rule, `last_evaluation`, worker and source timestamps | Keep worker running; confirm threshold crossing and cooldown |
| No email alert | Email delivery is not implemented | Use **In-app notifications**; do not configure imaginary SMTP settings |
| Invitation failed | Check 64-character recipient principal, expiry and role | Use `/api/v1/platform/me` account ID, refresh invitation |
| Shared report no longer visible | Check workspace membership/unshare status | Confirm access permissions; re-invite if appropriate |
| Port 8000 in use on Windows | `Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue` | Stop conflicting process or deliberately change port |
| Redis refused | `docker ps`; `docker exec financialagentia-redis redis-cli ping` for manual Redis | Start Redis and check host vs Compose network URL |
| Changes to env ignored | Review compose `env_file`, container env overrides and worker config | Recreate containers; restart both manual processes |
| Cannot find saved report | Check `RESEARCH_RETENTION_SECONDS` and actor | Reports expire by TTL; make authorized exports before expiration |

## Docker log examples

For local overlay, from repository root:

```bash
docker compose -f docker-compose.research.yml -f docker-compose.local.yml ps
docker compose -f docker-compose.research.yml -f docker-compose.local.yml logs --tail=150 api
docker compose -f docker-compose.research.yml -f docker-compose.local.yml logs --tail=150 worker
docker compose -f docker-compose.research.yml -f docker-compose.local.yml logs --tail=150 redis
```

## Never hide these failures

- Do not disable authorization to "fix" a public-deployment 401.
- Do not set wildcards in CORS origins.
- Do not substitute mocked prices for a failed real data provider.
- Do not delete Redis volumes to clear a stalled job without first investigating and backing up.
- Do not confuse a healthy HTTP server with operational workers and paid-provider connectivity.

See [runbook](runbook.md) and [model/provider setup](../installation/configuration.md).
