# phases/plan.py
import json
from typing import TYPE_CHECKING, List, Dict, Any
import structlog
from datetime import datetime

from ..schemas import Plan, PlanTask, TaskType, ToolCall
from ...model.llm import call_llm, LLMError, LLMParseError

if TYPE_CHECKING:
    from orchestrator import ExecutionContext

logger = structlog.get_logger()


class PlanPhase:
    def __init__(self, model: str):
        self.model = model
    
    async def run(self, context: "ExecutionContext") -> Plan:
        """
        Generate execution plan with dependency resolution.
        Uses structured output for reliable parsing.
        """
        iteration = context.iteration + 1  # iterations are 0-indexed in context
        system_prompt = self._build_system_prompt(iteration)
        user_prompt = self._build_user_prompt(context, iteration)
        
        try:
            # Generate structured plan using Pydantic model
            plan = await call_llm(
                prompt=user_prompt,
                system_prompt=system_prompt,
                model=self.model,
                output_model=Plan,
                temperature=0.2,
                max_tokens=2000
            )
            
            # Prefix task IDs with iteration to ensure uniqueness across loops
            self._normalize_task_ids(plan, iteration)
            
            # Validate no circular dependencies
            self._validate_dependencies(plan)
            
            logger.info(
                "plan_generated",
                run_id=context.run_id,
                iteration=iteration,
                task_count=len(plan.tasks),
                summary=plan.summary[:100]
            )
            
            return plan
            
        except (LLMError, LLMParseError) as e:
            logger.error("plan_generation_failed", run_id=context.run_id, error=str(e))
            return self._create_fallback_plan(context.query, iteration)
        except Exception as e:
            logger.error("plan_unexpected_error", run_id=context.run_id, error=str(e))
            return self._create_fallback_plan(context.query, iteration)
    
    def _build_system_prompt(self, iteration: int) -> str:
        base = f"""You are a financial research planner. Current date: {datetime.now().isoformat()}.

Create a structured research plan. Rules:
- Break complex queries into specific, verifiable tasks (max 10 tasks)
- Each task needs: id (simple like "1", "2a"), description, task_type (research/calculation/validation/api_call)
- tool_calls: Array of tool invocations needed (tool name + parameters)
- depends_on: Array of task IDs that must complete before this one
- max_retries: How many times to retry on failure (0-3)

Available Tools:
- get_stock_price(ticker: str): Current price data
- get_financials(ticker: str, period: str): Income statement, balance sheet
- calculate_metrics(data: dict, metric: str): ROE, P/E, etc.
- search_news(query: str, days: int): Recent news articles
- compare_entities(entities: list): Comparative analysis

Output must follow the Plan schema with summary and tasks array."""
        
        if iteration > 1:
            base += "\n\nThis is a REPLANNING iteration. Focus on:\n"
            base += "- Filling gaps from previous failed tasks\n"
            base += "- Correcting errors identified in reflection\n"
            base += "- Adding validation steps for uncertain data"
        
        return base
    
    def _build_user_prompt(self, context: "ExecutionContext", iteration: int) -> str:
        parts = [
            f"User Query: {context.query}",
            f"Intent: {context.understanding.intent if context.understanding else 'N/A'}",
            f"Entities Found: {[f'{e.type}={e.value}' for e in context.understanding.entities] if context.understanding else 'None'}"
        ]
        
        if context.completed_plans:
            parts.append("\nPrevious Plans (last 2):")
            for i, plan in enumerate(context.completed_plans[-2:], 1):
                parts.append(f"\nAttempt {i}: {plan.summary}")
                for task in plan.tasks:
                    # Use string status since TaskStatus enum doesn't exist
                    status = "✓" if task.status == "completed" else "✗" if task.status == "failed" else "○"
                    parts.append(f"  {status} {task.id}: {task.description}")
        
        # Use ReflectionOutput (not ReflectionResult)
        if context.reflection and context.reflection.suggested_next_steps:
            parts.append(f"\nReflection Guidance: {context.reflection.suggested_next_steps}")
            if context.reflection.tasks_to_retry:
                parts.append(f"Tasks to Retry: {', '.join(context.reflection.tasks_to_retry)}")
        
        return "\n".join(parts)
    
    def _normalize_task_ids(self, plan: Plan, iteration: int):
        """Ensure task IDs are unique across iterations and dependencies are updated."""
        id_mapping = {}
        
        for task in plan.tasks:
            old_id = task.id
            new_id = f"iter{iteration}_{old_id}"
            id_mapping[old_id] = new_id
            task.id = new_id
        
        # Update dependencies to use new IDs
        for task in plan.tasks:
            if task.depends_on:
                task.depends_on = [
                    id_mapping.get(dep, f"iter{iteration}_{dep}") 
                    for dep in task.depends_on
                ]
    
    def _validate_dependencies(self, plan: Plan):
        """Detect circular dependencies using DFS."""
        graph = {task.id: set(task.depends_on) for task in plan.tasks}
        visited = set()
        rec_stack = set()
        
        def has_cycle(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            
            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    if has_cycle(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True
            
            rec_stack.remove(node)
            return False
        
        for node in graph:
            if node not in visited:
                if has_cycle(node):
                    raise ValueError(f"Circular dependency detected in plan involving {node}")
    
    def _create_fallback_plan(self, query: str, iteration: int) -> Plan:
        """Minimal fallback plan when LLM fails."""
        return Plan(
            summary=f"Direct research approach (iteration {iteration})",
            tasks=[
                PlanTask(
                    id=f"iter{iteration}_fallback",
                    description=f"Research and answer: {query[:100]}",
                    task_type=TaskType.RESEARCH,
                    tool_calls=[ToolCall(tool="search_news", parameters={"query": query, "days": 30})]
                )
            ]
        )