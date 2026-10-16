# FinancialAgentia

FinancialAgentia is an evidence-led financial research application with a React browser workspace, FastAPI API, Redis-backed research jobs and an independent Python worker. It also includes financial-data inspection, explicit saved research context, read-only portfolio analysis, periodic monitoring and permissioned team research.

**Current supported runtime:** Bun-built web assets served by FastAPI → Redis durable execution → Python research worker → configured AI and financial providers. The older Bun/Ink terminal client remains available as a backend client; it is not a second research engine.

## Start here

- [Documentation index](docs/README.md)
- [Quick start: Docker or manual](docs/getting-started/quick-start.md)
- [Windows PowerShell setup](docs/getting-started/windows-setup.md)
- [Linux/macOS setup](docs/getting-started/linux-macos-setup.md)
- [Configuration, authentication and providers](docs/installation/configuration.md)
- [Browser feature user guide](docs/features/user-guide.md)
- [Operations, upgrade and backup](docs/operations/runbook.md)
- [Troubleshooting](docs/operations/troubleshooting.md)
- [Production deployment](docs/deployment/production-guide.md)
- [Tests and verification status](docs/testing/verification.md)

### Local Docker quick start

Requires Docker with Compose, OpenAI or a reachable Ollama model, and licensed Financial Datasets credentials for market research. From the repository root:

```powershell
git clone https://github.com/SteveParadox/FinancialAgentia.git
cd FinancialAgentia
Copy-Item python-backend\.env.example python-backend\.env
# Edit python-backend\.env: set OPENAI_API_KEY, LLM_MODEL, FINANCIAL_DATASETS_API_KEY.
docker compose -f docker-compose.research.yml -f docker-compose.local.yml config
docker compose -f docker-compose.research.yml -f docker-compose.local.yml up --build -d
docker compose -f docker-compose.research.yml -f docker-compose.local.yml ps
```

Open **http://127.0.0.1:8000/** and select **Open workspace** with a blank access token. This *local-only* overlay deliberately disables authentication, and the API port is bound to the host loopback address. **Never deploy the overlay publicly or use it with private production data.** Docker's base Compose file is production-oriented, requires properly provisioned authentication, and does **not** support a blank browser token.

Run `docker compose -f docker-compose.research.yml -f docker-compose.local.yml logs -f api worker` to diagnose startup. `/health` checks the HTTP process; `/ready` requires Redis, a recent worker heartbeat, registered tools and configured model credentials. Neither endpoint proves paid market-data access.

The Windows command `Copy-Item` above is PowerShell-specific. See the [Linux/macOS guide](docs/getting-started/linux-macos-setup.md) for `cp` and shell commands.

## What is implemented

| Area | Current scope |
| --- | --- |
| Research | Company, comparison, historical-price and general scoped jobs; progress, cancellation, retry, saved evidence-backed results |
| Model providers | OpenAI and Ollama local/cloud; additional legacy optional adapters subject to compatible packages |
| Financial data | Financial Datasets market/fundamental data and optionally configured SEC filing facts; no blanket claim of live quotations |
| Context | Explicitly saved reports for authorized follow-up; not a general semantic/vector memory service |
| Portfolio | Long-only, single-currency transaction ledger; valuation, scenario shocks and a limited historical risk proxy |
| Monitoring | UTC periodic price crossing rules and recurring research; in-app notifications only |
| Collaboration | Authenticated workspaces, invitations, roles, frozen shared report snapshots, comments, review state and JSON/Markdown/CSV exports |

**Important limitations:** The app does not implement brokerage trading, self-service web signup, email notification delivery, a general FX conversion engine or verified live-market entitlement. A risk proxy calculated with current quantities is not actual investor performance. Research results and financial data can be partial or unavailable.

## Development and tests

The root `package.json` contains `build:web`, `typecheck:web`, `test:web`, `typecheck` and `test`. The Python backend has a separate virtualenv and `requirements-dev.txt`. Follow the [manual setup](docs/getting-started/quick-start.md#manual-development-startup) and [verification guide](docs/testing/verification.md), not the legacy terminal-first installation text.

The companion legacy terminal client is started with `bun start` once the API and worker are operational; its own credential settings live in the root `env.example`.

## Project references

- [Research API](docs/api/research-api.md)
- [Target architecture](docs/architecture/target-architecture.md)
- [Existing execution operations](python-backend/END_TO_END_OPERATIONS.md)
- [Existing implementation report](docs/implementation-report.md)
- [Current docs validation](docs/documentation-validation.md)

Financial research is informational, not a guarantee of investment outcomes.
