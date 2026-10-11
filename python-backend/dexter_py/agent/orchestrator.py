"""Python API agent: one per-request context and one canonical task execution phase.

The TypeScript financial tool catalog is not exposed here.  Python tool
dependencies must be registered explicitly before use.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Mapping
import asyncio
import uuid

import structlog

from .schemas import Plan, Understanding, ReflectionOutput
from .phases.execute import ExecutePhase
from ..exceptions import OrchestratorError
from ..model.llm_integration import StructuredLLMClient

logger = structlog.get_logger(__name__)


class StopReason(str, Enum):
    COMPLETE = "reflection_complete"
    MAX_ITERATIONS = "max_iterations"
    NO_PROGRESS = "no_progress"
    ERROR = "error"


@dataclass
class ExecutionContext:
    run_id: str
    query: str
    model: str
    conversation_context: str = ""
    understanding: Optional[Understanding] = None
    current_plan: Optional[Plan] = None
    completed_plans: List[Plan] = field(default_factory=list)
    task_results: Dict[str, Any] = field(default_factory=dict)
    reflection: Optional[ReflectionOutput] = None
    iteration: int = 0
    stop_reason: Optional[StopReason] = None


class Orchestrator:
    """Coordinates understanding, planning, real tools, reflection and answer."""

    PHASE_TIMEOUTS = {
        "understand": 30.0, "plan": 45.0, "execute": 120.0,
        "reflect": 30.0, "answer": 60.0,
    }
    MAX_ANSWER_CHARS = 100000

    def __init__(
        self,
        model: str,
        max_iterations: int = 3,
        llm_client: Optional[StructuredLLMClient] = None,
        tool_executor: Optional[Any] = None,
        phase_timeouts: Optional[Mapping[str, float]] = None,
    ):
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if type(max_iterations) is not int or not 1 <= max_iterations <= 10:
            raise ValueError("max_iterations must be an integer between 1 and 10")
        self.model = model
        self.max_iterations = max_iterations
        configured_timeouts = dict(self.PHASE_TIMEOUTS)
        if phase_timeouts is not None:
            for phase, value in phase_timeouts.items():
                if phase not in configured_timeouts or isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 300:
                    raise ValueError(f"Invalid phase timeout for {phase!r}")
                configured_timeouts[phase] = float(value)
        self.phase_timeouts = configured_timeouts
        self.llm = llm_client if llm_client is not None else StructuredLLMClient(model)
        if not callable(getattr(self.llm, "complete_structured", None)):
            raise ValueError("llm_client must implement complete_structured")
        if not callable(getattr(self.llm, "stream_text", None)):
            raise ValueError("llm_client must implement stream_text")
        self.execute_phase = ExecutePhase(model=model)
        if tool_executor is not None:
            self.configure_tools(tool_executor)

    def configure_tools(self, tool_executor: Any) -> None:
        self.execute_phase.configure_tools(tool_executor)

    async def run(self, query: str, message_history: Optional[Any] = None) -> str:
        """Run without mutating shared history; API/service owns persistence."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be non-empty")
        if len(query) > 10000:
            raise ValueError("query exceeds 10000 characters")
        history_text = ""
        if message_history is not None:
            formatter = getattr(message_history, "format_for_planning", None)
            if not callable(formatter):
                raise TypeError("message_history must implement format_for_planning()")
            history_text = formatter()[-8000:]
        context = ExecutionContext(
            run_id=str(uuid.uuid4()), query=query, model=self.model,
            conversation_context=history_text,
        )
        logger.info("orchestrator_start", run_id=context.run_id)
        try:
            if self.execute_phase.tool_executor is None:
                raise RuntimeError("Tool executor not configured")
            context.understanding = await asyncio.wait_for(self._understand(context), timeout=self.phase_timeouts["understand"])
            for iteration in range(1, self.max_iterations + 1):
                context.iteration = iteration
                context.current_plan = await asyncio.wait_for(self._plan(context), timeout=self.phase_timeouts["plan"])
                # Reject malformed plans before any execution, not after partial success.
                context.current_plan.get_execution_order()
                await asyncio.wait_for(self._execute(context), timeout=self.phase_timeouts["execute"])
                context.completed_plans.append(context.current_plan)
                context.reflection = await asyncio.wait_for(self._reflect(context), timeout=self.phase_timeouts["reflect"])
                incomplete_work = any(
                    task.status != "completed" for task in context.current_plan.tasks
                )
                if (context.reflection.is_complete and
                    context.reflection.confidence >= 0.8 and not incomplete_work):
                    context.stop_reason = StopReason.COMPLETE
                    break
                if not context.current_plan.tasks or all(
                    task.status in ("failed", "skipped") for task in context.current_plan.tasks
                ):
                    context.stop_reason = StopReason.NO_PROGRESS
                    break
            else:
                context.stop_reason = StopReason.MAX_ITERATIONS

            if (context.completed_plans and
                any(task.tool_calls for plan in context.completed_plans for task in plan.tasks) and
                not any(task.status == "completed"
                        for plan in context.completed_plans for task in plan.tasks)):
                raise OrchestratorError("All requested financial research tool tasks failed or were skipped")

            return await asyncio.wait_for(self._answer(context), timeout=self.phase_timeouts["answer"])
        except Exception:
            context.stop_reason = StopReason.ERROR
            logger.exception("orchestrator_failed", run_id=context.run_id)
            raise
        finally:
            logger.info(
                "orchestrator_end", run_id=context.run_id,
                iterations=context.iteration,
                stop_reason=context.stop_reason.value if context.stop_reason else None,
            )

    async def _understand(self, context: ExecutionContext) -> Understanding:
        prompt = "Query: " + context.query
        if context.conversation_context:
            prompt += "\nPrevious conversation:\n" + context.conversation_context
        return await self.llm.complete_structured(
            prompt=prompt,
            output_model=Understanding,
            system_prompt=(
                "You are a financial query analyzer. Extract intent, entities, "
                "temporal constraints and complexity. Use only provided context."
            ),
            temperature=0.1,
        )

    async def _plan(self, context: ExecutionContext) -> Plan:
        available = getattr(self.execute_phase.tool_executor, "tools", {})
        tool_names = sorted(available) if isinstance(available, dict) else []
        system_prompt = (
            "You are a financial research planner. Produce a Plan with unique task IDs, "
            "valid dependencies and executable tool calls. Do not invent tool names, "
            "results or research. Available Python tools: " + repr(tool_names) + ". "
            "When no tools are available, create an empty task list and explain "
            "data limitations in the summary. Never fabricate financial data. "
            "Current date: " + datetime.now(timezone.utc).date().isoformat()
        )
        prior = "\n".join(
            f"{p.summary}: " + ", ".join(f"{t.id}={t.status}" for t in p.tasks)
            for p in context.completed_plans[-2:]
        )
        prompt = (
            f"Query: {context.query}\n"
            f"Understanding: {context.understanding.model_dump_json()}\n"
            f"Iteration: {context.iteration}\nPrevious plans: {prior}\n"
            f"Reflection: {context.reflection.model_dump_json() if context.reflection else 'none'}\n"
            f"Conversation context: {context.conversation_context}"
        )
        plan = await self.llm.complete_structured(
            prompt=prompt, output_model=Plan, system_prompt=system_prompt,
            temperature=0.2,
        )
        prefix = f"iter{context.iteration}_"
        original_ids = [task.id for task in plan.tasks]
        if len(original_ids) != len(set(original_ids)):
            raise ValueError("Duplicate task IDs in generated plan")
        mapping = {task_id: task_id if task_id.startswith(prefix) else prefix + task_id
                   for task_id in original_ids}
        for task in plan.tasks:
            task.depends_on = [mapping.get(dep, dep) for dep in task.depends_on]
            task.id = mapping[task.id]
            # Runtime evidence belongs to the executor, never to the planner LLM.
            task.status = "pending"
            task.attempt_count = 0
            task.result = None
            task.error = None
            task.start_time = None
            task.end_time = None
        plan.get_execution_order()
        return plan

    async def _execute(self, context: ExecutionContext) -> None:
        await self.execute_phase.run(context)

    async def _reflect(self, context: ExecutionContext) -> ReflectionOutput:
        evidence = "\n".join(
            f"{task.id} | {task.status} | result={task.result!r} | error={task.error!r}"
            for plan in context.completed_plans for task in plan.tasks
        )
        return await self.llm.complete_structured(
            prompt=f"Query: {context.query}\nExecution evidence:\n{evidence or 'No tool evidence'}",
            output_model=ReflectionOutput,
            system_prompt=(
                "Assess completeness based strictly on execution evidence. "
                "Do not claim data was retrieved if tools were not executed. "
                "Identify missing information and failed tasks."
            ),
            temperature=0.2,
        )

    async def _answer(self, context: ExecutionContext) -> str:
        evidence = "\n".join(
            f"{task.id} ({task.status}): {task.result!r}; error={task.error!r}"
            for plan in context.completed_plans for task in plan.tasks
        )
        prompt = (
            f"User query: {context.query}\n"
            f"Prior conversation: {context.conversation_context}\n"
            f"Research execution: {evidence or 'No external financial research performed.'}\n"
            f"Reflection: {context.reflection.model_dump_json() if context.reflection else 'none'}"
        )
        parts: List[str] = []
        total_chars = 0
        async for chunk in self.llm.stream_text(
            prompt=prompt,
            system_prompt=(
                "You are FinancialAgentia. Answer using the evidence supplied. "
                "Never invent prices, sources, citations or tool results. "
                "State explicitly when financial data is unavailable."
            ),
        ):
            if chunk:
                parts.append(chunk)
                total_chars += len(chunk)
                if total_chars > self.MAX_ANSWER_CHARS:
                    raise OrchestratorError("Answer exceeds maximum permitted length")
        answer = "".join(parts)
        if not answer.strip():
            raise OrchestratorError("The answer model returned an empty response")
        return answer
