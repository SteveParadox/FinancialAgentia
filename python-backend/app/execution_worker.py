"""Standalone, competing-worker-safe FinancialAgentia execution processor.

Start independently using: python -m app.execution_worker
Requires shared Redis; NEVER starts a local single-process fallback.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
import signal
import time
import uuid
import structlog

logger = structlog.get_logger(__name__)

from dexter_py.agent.contracts import AgentExecution
from dexter_py.agent.execution_registry import ExecutionRegistry
from dexter_py.agent.financial_evidence import (
    ProposedClaims, normalize_tool_evidence, validate_claims, verified_report,
)


class ReadOnlyHistory:
    def __init__(self, turns: list[dict]):
        self.turns = turns

    def format_for_planning(self) -> str:
        return "\n".join(
            "User: " + turn.get("query", "")[:1500] + "\nAssistant: " + turn.get("answer", "")[:3000]
            for turn in self.turns[-8:]
        )[-8000:]


class ExecutionWorker:
    def __init__(self, registry: ExecutionRegistry, orchestrator,
                 *, lease_seconds: int = 20, poll_seconds: float = 0.5):
        if lease_seconds < 5 or poll_seconds <= 0:
            raise ValueError("Invalid worker timing")
        self.registry = registry
        self.orchestrator = orchestrator
        self.worker_id = str(uuid.uuid4())
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self.stopping = asyncio.Event()

    async def _watch(self, execution_id: str, generation: int,
                     job: asyncio.Task):
        while not job.done():
            await asyncio.sleep(min(1.0, self.lease_seconds / 4))
            try:
                status = await self.registry.heartbeat(
                    execution_id, self.worker_id, generation, self.lease_seconds)
            except Exception:
                # A shared-state outage cannot grant permission to keep running.
                job.cancel()
                return
            if status != "OK":
                job.cancel()
                return

    async def _execute(self, execution_id: str, generation: int) -> dict:
        record = await self.registry.redis.hgetall(self.registry._key(execution_id))
        if record.get("generation") != str(generation) or record.get("owner") != self.worker_id:
            raise RuntimeError("Worker ownership lost before research")
        history = ReadOnlyHistory(await self.registry.history(record["principal"], record["session_id"]))
        raw_results: list[dict] = []
        artifacts: dict = {}
        oversized_evidence_count = 0

        def collect(result: dict):
            nonlocal oversized_evidence_count
            if len(raw_results) >= 50:
                oversized_evidence_count += 1
                return
            encoded = json.dumps(result, default=str)
            if len(encoded) > 300_000:
                oversized_evidence_count += 1
                return
            raw_results.append(result)

        redis_time = await self.registry.redis.time()
        authoritative_now = float(redis_time[0]) + float(redis_time[1]) / 1_000_000
        timeout = max(0.001, float(record["deadline_at"]) - authoritative_now)
        # The per-execution context and result collector are never shared.
        async with asyncio.timeout(timeout):
            async def report_phase(phase: str):
                status = await self.registry.publish_phase(
                    execution_id, self.worker_id, generation, phase)
                if status != "OK":
                    raise asyncio.CancelledError

            async def checkpoint(snapshot: dict):
                nonlocal artifacts
                artifacts = snapshot
                status = await self.registry.checkpoint(execution_id, self.worker_id, generation, snapshot)
                if status != "OK":
                    raise asyncio.CancelledError

            answer = await self.orchestrator.run(
                record["query"], history,
                evidence_collector=collect, progress_callback=report_phase, checkpoint_callback=checkpoint)
            await report_phase("validate")
            sources, evidence = normalize_tool_evidence(raw_results)
            limitations: list[str] = []
            executions = artifacts.get("agent_executions", [])
            if any(item["status"] != "completed" for item in executions):
                limitations.append("Some requested research tasks failed or were skipped.")
            if artifacts.get("stop_reason") != "reflection_complete":
                limitations.append("Research coverage was not confirmed complete within the execution budget.")
            if any(source.adjustment_status == "unspecified" for source in sources):
                limitations.append("Corporate-action adjustment policy was not supplied by the provider.")
            if any(row.metric in {"price", "close", "sma"} and not row.currency for row in evidence):
                limitations.append("Some price records lack currency; no live-price claim can be verified.")
            if any(call.get("result", {}).get("completeness") == "partial"
                   for task in raw_results for call in task.get("tool_results", [])
                   if isinstance(call.get("result"), dict)):
                limitations.append("Provider coverage is incomplete or additional pages were not retrieved.")
            if oversized_evidence_count:
                limitations.append('Some oversized research evidence was excluded.')
            proposed = ProposedClaims(claims=[])
            if evidence:
                try:
                    # Untrusted model prose is advisory. Only matching typed
                    # numerical claims survive deterministic validation.
                    proposed = await asyncio.wait_for(
                        self.orchestrator.llm.complete_structured(
                            prompt=(
                                "Rewrite only directly supported financial facts as claims. "
                                "Use source evidence IDs verbatim and exactly one numeric "
                                "assertion per statement. No unsupported claims.\n"
                                "Model draft (UNTRUSTED): " + answer[:6000] + "\n"
                                "Verified evidence: " + json.dumps(
                                    [item.model_dump(mode="json") for item in evidence[:50]]
                                )[:16000]
                            ),
                            output_model=ProposedClaims,
                            system_prompt=(
                                "Return structured, evidence-bound claims only. "
                                "You must not invent source IDs, prices or other numbers."
                            ),
                            temperature=0,
                        ), timeout=20)
                except (Exception, asyncio.TimeoutError):
                    limitations.append("Structured model claim generation unavailable.")
            claims, rejected = validate_claims(proposed, evidence)
            if rejected:
                limitations.append(f"{len(rejected)} unsupported or inconsistent claims rejected.")
            report = verified_report(
                execution_id, record["session_id"], sources, evidence, claims, limitations)
            report.agent_executions = []
            for item in executions:
                agent_sources, agent_evidence = normalize_tool_evidence([item.get("findings", {})])
                summary = {**item,
                           "findings": {"validated_metric_count": len(agent_evidence),
                                        "validated_source_count": len(agent_sources)},
                           "evidence_references": [row.evidence_id for row in agent_evidence
                                                   if row.evidence_id in {e.evidence_id for e in evidence}]}
                report.agent_executions.append(AgentExecution.model_validate(summary))
            return report.model_dump(mode="json")

    async def process(self, execution_id: str, generation: int) -> str:
        job = asyncio.create_task(self._execute(execution_id, generation))
        watcher = asyncio.create_task(self._watch(execution_id, generation, job))
        outcome = "FAILED"
        result = None
        error = "AGENT_EXECUTION_FAILED"
        try:
            result = await job
            outcome = "FAILED" if result["status"] == "INSUFFICIENT_EVIDENCE" else "SUCCEEDED"
            error = "INSUFFICIENT_EVIDENCE" if outcome == "FAILED" else ""
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
                raise  # Worker shutdown leaves the lease recoverable.
            # A cancellation may have been requested via another API worker,
            # or a lease may have been lost. Fenced finish rejects stale owners.
            outcome = "CANCELLED"
            error = "EXECUTION_CANCELLED"
        except TimeoutError:
            outcome = "TIMED_OUT"
            error = "EXECUTION_TIMED_OUT"
        except Exception as exc:
            logger.error("execution_failed", execution_id=execution_id, worker_id=self.worker_id,
                         generation=generation, error_category=type(exc).__name__)
            outcome = "FAILED"
            error = "AGENT_EXECUTION_FAILED"
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        committed = await self.registry.finish(execution_id, self.worker_id, generation,
                                               outcome, result=result, failure_code=error)
        logger.info("execution_finished", execution_id=execution_id, worker_id=self.worker_id,
                    generation=generation, outcome=committed)
        return committed

    async def run(self):
        async def pulse():
            while not self.stopping.is_set():
                await self.registry.worker_heartbeat(self.worker_id)
                await asyncio.sleep(5)
        heartbeat = asyncio.create_task(pulse())
        try:
            await self._run_jobs()
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await self.registry.redis.zrem(self.registry.prefix + "workers", self.worker_id)

    async def _run_jobs(self):
        while not self.stopping.is_set():
            claimed = await self.registry.claim(self.worker_id, self.lease_seconds)
            if not claimed:
                await asyncio.sleep(self.poll_seconds)
                continue
            processing = asyncio.create_task(self.process(*claimed))
            stopping = asyncio.create_task(self.stopping.wait())
            try:
                done, _ = await asyncio.wait({processing, stopping}, return_when=asyncio.FIRST_COMPLETED)
                if stopping in done and not processing.done():
                    try:
                        await asyncio.wait_for(asyncio.shield(processing), timeout=5)
                    except TimeoutError:
                        processing.cancel()
                await asyncio.gather(processing, return_exceptions=True)
            finally:
                stopping.cancel()
                if not processing.done():
                    processing.cancel()
                await asyncio.gather(stopping, processing, return_exceptions=True)


async def main():
    from app.main import app
    async with app.router.lifespan_context(app):
        registry = app.state.execution_registry
        if registry is None:
            raise RuntimeError("REDIS_URL required to run financial research worker")
        if not await registry.health():
            raise RuntimeError("Execution Redis is unavailable")
        worker = ExecutionWorker(registry, app.state.orchestrator)
        loop = asyncio.get_running_loop()
        for name in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(name, worker.stopping.set)
            except NotImplementedError:
                pass
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
