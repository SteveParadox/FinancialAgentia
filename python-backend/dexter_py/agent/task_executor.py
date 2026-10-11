"""Compatibility adapter for callers of the old TaskExecutor API.

Delegates to the single ExecutePhase implementation; never fabricates results.
"""
import uuid
from typing import Any, Dict, Optional

from .schemas import Plan
from .orchestrator import ExecutionContext
from .phases.execute import ExecutePhase


class TaskExecutor:
    def __init__(self, *, model: str, tool_executor: Any,
                 execute_phase: Optional[ExecutePhase] = None,
                 context_manager: Any = None) -> None:
        self.model = model
        self.execute_phase = execute_phase or ExecutePhase(model)
        self.execute_phase.configure_tools(tool_executor)
        self.context_manager = context_manager

    async def execute_tasks(
        self, query: str, plan: Any, understanding: Any,
        task_results: Dict[str, Any], callbacks: Optional[Any] = None,
    ) -> None:
        validated = plan if isinstance(plan, Plan) else Plan.model_validate(plan)
        validated.get_execution_order()
        context = ExecutionContext(
            run_id=str(uuid.uuid4()), query=query, model=self.model,
            understanding=understanding, current_plan=validated,
            task_results=task_results,
        )
        await self.execute_phase.run(context)
        if callbacks is not None:
            for task in validated.tasks:
                callback = getattr(callbacks, "on_task_complete"
                    if task.status == "completed" else "on_task_error", None)
                if callable(callback):
                    callback(task.id, task.result if task.status == "completed"
                             else RuntimeError(task.error or task.status))
