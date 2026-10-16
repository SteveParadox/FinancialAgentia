# Windows 10/11: PowerShell setup

Use **PowerShell**, not Bash. The easiest route is [Docker local development](quick-start.md#docker-local-development) with Docker Desktop running. WSL2 support may be required by Docker Desktop.

## Check prerequisites

```powershell
git --version
python --version
bun --version
docker --version
docker compose version
```

Use Python 3.11+ and Bun 1.4.3+. Install Git, Python, Bun and Docker Desktop as needed; restart your terminal after installation. These commands are checks, not installers.

## Manual installation

From a PowerShell terminal in your projects folder:

```powershell
git clone https://github.com/SteveParadox/FinancialAgentia.git
Set-Location FinancialAgentia
bun install --frozen-lockfile
bun run build:web
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r python-backend\requirements-dev.txt
Copy-Item python-backend\.env.example python-backend\.env
notepad python-backend\.env
```

Using `.\.venv\Scripts\python.exe` directly avoids PowerShell execution-policy issues. If `py` is unavailable, replace `py -3` with `python`, but first confirm the installed version.

Set your model key and financial provider key. Leave `BACKEND_ENV=development`, `BACKEND_API_KEY=` and `JWT_SECRET=` for **local loopback-only** development. Use `REDIS_URL=redis://127.0.0.1:6379/0` for host-run Python processes.

Start Redis through Docker, if not already running:

```powershell
docker run -d --name financialagentia-redis -p 127.0.0.1:6379:6379 -v financialagentia-redis-data:/data redis:7.4-alpine redis-server --appendonly yes
docker exec financialagentia-redis redis-cli ping
```

A repeat run with the same name fails because the container already exists. For a stopped container use `docker start financialagentia-redis`. If Redis is already installed and configured, reuse it instead.

**Terminal 1: API** (from repository root):

```powershell
Set-Location python-backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Terminal 2: Worker** (open a new PowerShell terminal in the repository root):

```powershell
Set-Location python-backend
..\.venv\Scripts\python.exe -m app.execution_worker
```

**Terminal 3: Health checks** (optional):

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
```

Open `http://127.0.0.1:8000/`. The web assets were already compiled by `bun run build:web`; no separate frontend development server is required.

## Common Windows problems

| Symptom | Check / fix |
| --- | --- |
| `py` or `python` not found | Install a supported Python and reopen PowerShell; inspect `py -0p` |
| `bun` not found | Ensure Bun is installed and on `PATH`; reopen terminal |
| Script activation blocked | Use `.\.venv\Scripts\python.exe` instead of activating |
| Docker unavailable | Start Docker Desktop; check `docker info` |
| Redis refused | Check `docker ps` and `docker exec financialagentia-redis redis-cli ping` |
| Port 8000 already used | `Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue` |
| Frontend 503 | Run `bun run build:web` from repository root |
| `/ready` 503 | Start worker, check Redis and model config; see [troubleshooting](../operations/troubleshooting.md) |
| Local Ollama unreachable from container | Container `localhost` is not host `localhost`; use a reachable host address with correct Ollama bind/security settings |

Use `Ctrl+C` to stop API and worker gracefully. `docker stop financialagentia-redis` stops the Redis container without deleting its volume. Do not remove that volume during routine troubleshooting.

For Docker-only startup, see [quick start](quick-start.md). For configuration and API credentials, see [configuration](../installation/configuration.md).
