"""Canonical Python task execution phase; no simulated tool results."""
import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog

from ..schemas import Plan
from ...exceptions import TaskExecutionError
from ...tools.financial_datasets import FinancialDataError

logger = structlog.get_logger(__name__)


class ExecutePhase:
    def __init__(self, model: str, max_concurrency: int = 5):
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self.model = model
        self.max_concurrency = max_concurrency
        self.tool_executor = None

    def configure_tools(self, tool_executor: Any) -> None:
        if tool_executor is None or not callable(getattr(tool_executor, "execute", None)):
            raise ValueError("A tool executor with execute(name, parameters) is required")
        self.tool_executor = tool_executor

    async def run(self, context: Any) -> None:
        if context.current_plan is None:
            raise ValueError("Cannot execute without a plan")
        plan: Plan = context.current_plan
        # Validate ALL dependencies before executing ANY tasks.
        batches = plan.get_execution_order()
        if self.tool_executor is None:
            raise RuntimeError("Tool executor not configured")
        registered = getattr(self.tool_executor, "tools", None)
        if isinstance(registered, dict):
            unknown = sorted({
                call.tool
                for task in plan.tasks
                for call in task.tool_calls
                if call.tool not in registered
            })
            if unknown:
                raise ValueError("Unknown tools in plan: " + ", ".join(unknown))

        by_id = {task.id: task for task in plan.tasks}
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def execute_one(task_id: str) -> None:
            task = by_id[task_id]
            async with semaphore:
                blocked = [dep for dep in task.depends_on
                           if by_id[dep].status != "completed"]
                if blocked:
                    task.status = "skipped"
                    task.error = "Prerequisites not completed: " + ", ".join(blocked)
                    context.task_results[task.id] = {"status": "skipped", "error": task.error}
                    return
                if not task.tool_calls:
                    # A description is not research evidence.
                    task.status = "skipped"
                    task.error = "Task has no executable tool calls"
                    context.task_results[task.id] = {"status": "skipped", "error": task.error}
                    return

                task.start_time = datetime.now(timezone.utc)
                task.status = "running"
                try:
                    # max_retries is additional retries AFTER the initial attempt.
                    successful_calls = {}
                    for attempt in range(task.max_retries + 1):
                        task.attempt_count = attempt + 1
                        try:
                            tool_results = []
                            for index, call in enumerate(task.tool_calls):
                                if index not in successful_calls:
                                    result = await asyncio.wait_for(
                                        self.tool_executor.execute(call.tool, call.parameters),
                                        timeout=call.timeout,
                                    )
                                    successful_calls[index] = {
                                        "tool": call.tool, "result": result, "success": True
                                    }
                                tool_results.append(successful_calls[index])
                            task.result = {"tool_results": tool_results}
                            task.status = "completed"
                            task.error = None
                            context.task_results[task.id] = task.result
                            return
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            task.error = str(exc)
                            # Non-retryable: provider has already exhausted its own
                            # bounded HTTP retries, and bad arguments will not recover.
                            if isinstance(exc, (FinancialDataError, ValueError, TypeError)):
                                break
                            if attempt < task.max_retries:
                                logger.warning("task_retry", task_id=task.id, attempt=attempt + 1)
                    task.status = "failed"
                    context.task_results[task.id] = {"status": "failed", "error": task.error}
                except asyncio.CancelledError:
                    task.status = "failed"
                    task.error = "Execution cancelled"
                    context.task_results[task.id] = {"status": "failed", "error": task.error}
                    raise
                finally:
                    task.end_time = datetime.now(timezone.utc)

        for batch in batches:
            await asyncio.gather(*(execute_one(task_id) for task_id in batch))

        logger.info(
            "execution_complete",
            run_id=context.run_id,
            completed=sum(t.status == "completed" for t in plan.tasks),
            failed=sum(t.status == "failed" for t in plan.tasks),
            skipped=sum(t.status == "skipped" for t in plan.tasks),
        )
