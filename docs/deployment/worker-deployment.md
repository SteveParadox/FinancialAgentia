# Worker deployment

Build from repository root: `docker build -f python-backend/Dockerfile .`. The multistage image builds React/Bun assets and runs FastAPI as a non-root Python user. The same image runs a worker with `python -m app.execution_worker`. `docker compose -f docker-compose.research.yml up --build` starts Redis AOF, API and worker. Docker execution was not available for local verification; source/build commands and a clean Python environment were verified separately.

One worker process executes one job at a time; scale processes for concurrency. Independent DAG tasks have a five-task bound. Every process shares one persistence-enabled Redis primary, namespace and server model configuration. No worker runs a process-local durable fallback. Process-local provider caches coalesce identical reads within a worker, not globally across all workers.

API `/ready` requires Redis, worker heartbeat, registered financial tools and configured model credentials. These configuration checks do not prove paid-provider network access. Compose API health probes readiness. `/health` proves only HTTP liveness. Deploy behind HTTPS, retain `X-Accel-Buffering: no` and disable buffering for SSE; set proxy read timeouts above heartbeat intervals. A polling fallback remains available.

SIGTERM stops new claims and grants active work a five-second grace. On interruption the lease expires and a replacement can restore committed plan boundaries. Never run old unfenced workers alongside this registry. Back up Redis, rehearse restoration and validate report retention before exposing the product.
