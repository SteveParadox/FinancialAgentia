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

            answer = await self.orchestrator.run(
                record["query"], history,
                evidence_collector=collect, progress_callback=report_phase)
            await report_phase("validate")
            sources, evidence = normalize_tool_evidence(raw_results)
            limitations: list[str] = []
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
            return report.model_dump(mode="json")

    async def process(self, execution_id: str, generation: int) -> str:
        job = asyncio.create_task(self._execute(execution_id, generation))
        watcher = asyncio.create_task(self._watch(execution_id, generation, job))
        outcome = "FAILED"
        result = None
        error = "AGENT_EXECUTION_FAILED"
        try:
            result = await job
            outcome = "SUCCEEDED"
            error = ""
        except asyncio.CancelledError:
            # A cancellation may have been requested via another API worker,
            # or a lease may have been lost. Fenced finish rejects stale owners.
            outcome = "CANCELLED"
            error = "EXECUTION_CANCELLED"
        except TimeoutError:
            outcome = "TIMED_OUT"
            error = "EXECUTION_TIMED_OUT"
        except Exception:
            outcome = "FAILED"
            error = "AGENT_EXECUTION_FAILED"
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        return await self.registry.finish(execution_id, self.worker_id, generation,
                                          outcome, result=result, failure_code=error)

    async def run(self):
        while not self.stopping.is_set():
            claimed = await self.registry.claim(self.worker_id, self.lease_seconds)
            if not claimed:
                await asyncio.sleep(self.poll_seconds)
                continue
            await self.process(*claimed)


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
