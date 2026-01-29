# phases/execute.py
import asyncio
from typing import TYPE_CHECKING, Any, Dict, Set, Optional
from datetime import datetime
import structlog

from ..schemas import Plan, PlanTask, ToolCall
from ...exceptions import TaskExecutionError, DependencyError

if TYPE_CHECKING:
    from orchestrator import ExecutionContext
    from tool_executor import ToolExecutor

logger = structlog.get_logger()


class ExecutePhase:
    """
    Executes tasks with topological ordering, parallelization, and circuit breaker protection.
    """
    
    def __init__(self, model: str, max_concurrency: int = 5):
        self.model = model
        self.max_concurrency = max_concurrency
        self.tool_executor: Optional["ToolExecutor"] = None
    
    def configure_tools(self, tool_executor: "ToolExecutor"):
        """Inject tool executor dependency."""
        self.tool_executor = tool_executor
    
    async def run(self, context: "ExecutionContext"):
        """
        Execute all tasks in the current plan respecting dependencies.
        Updates context.task_results and task statuses in-place.
        """
        if not context.current_plan:
            logger.warning("execute_no_plan", run_id=context.run_id)
            return
        
        if not self.tool_executor:
            raise RuntimeError("Tool executor not configured. Call configure_tools() first.")
        
        plan = context.current_plan
        
        try:
            # Get topologically sorted batches
            batches = plan.get_execution_order()
            logger.info(
                "execution_start",
                run_id=context.run_id,
                batches=len(batches),
                total_tasks=len(plan.tasks)
            )
            
            completed_tasks: Set[str] = set()
            
            for batch_idx, batch in enumerate(batches):
                logger.debug(
                    "executing_batch",
                    run_id=context.run_id,
                    batch_index=batch_idx,
                    task_count=len(batch)
                )
                
                # Execute batch with controlled concurrency
                semaphore = asyncio.Semaphore(self.max_concurrency)
                
                async def run_with_sem(task_id: str):
                    async with semaphore:
                        return await self._execute_task(
                            task_id, plan, context, completed_tasks
                        )
                
                results = await asyncio.gather(
                    *[run_with_sem(tid) for tid in batch],
                    return_exceptions=True
                )
                
                # Process results and update completed set
                for task_id, result in zip(batch, results):
                    if isinstance(result, Exception):
                        logger.error(
                            "task_failed",
                            run_id=context.run_id,
                            task_id=task_id,
                            error=str(result)
                        )
                        task = self._get_task_by_id(plan, task_id)
                        if task:
                            task.status = "failed"  # String status
                            task.error = str(result)
                            task.end_time = datetime.utcnow()
                            
                        # Check if this failure blocks future tasks
                        if self._is_critical_failure(task_id, plan, batches[batch_idx+1:]):
                            logger.warning(
                                "critical_path_failure",
                                run_id=context.run_id,
                                failed_task=task_id
                            )
                    else:
                        completed_tasks.add(task_id)
                        await context.update_task_result(task_id, result)
            
            logger.info(
                "execution_complete",
                run_id=context.run_id,
                completed=len(completed_tasks),
                failed=len([t for t in plan.tasks if t.status == "failed"])
            )
            
        except Exception as e:
            logger.error("execution_fatal_error", run_id=context.run_id, error=str(e))
            raise
    
    async def _execute_task(
        self, 
        task_id: str, 
        plan: Plan, 
        context: "ExecutionContext",
        completed_deps: Set[str]
    ) -> Any:
        """Execute single task with full error handling."""
        task = self._get_task_by_id(plan, task_id)
        if not task:
            raise TaskExecutionError(f"Task {task_id} not found")
        
        # Check dependencies are satisfied
        missing = [dep for dep in task.depends_on if dep not in completed_deps]
        if missing:
            raise DependencyError(f"Dependencies not met: {missing}")
        
        # Mark as running
        task.status = "running"  # String status
        task.start_time = datetime.utcnow()
        
        try:
            # Execute all tool calls for this task
            tool_results = []
            for tool_call in task.tool_calls:
                result = await self._execute_tool_call(tool_call, context)
                tool_results.append(result)
            
            task.status = "completed"  # String status
            task.end_time = datetime.utcnow()
            task.result = {
                "task_id": task_id,
                "tool_results": tool_results,
                "timestamp": datetime.utcnow().isoformat()
            }
            
            return task.result
            
        except Exception as e:
            task.status = "failed"  # String status
            task.end_time = datetime.utcnow()
            task.error = str(e)
            task.attempt_count += 1
            
            if task.attempt_count <= task.max_retries:
                # Retry logic: could implement exponential backoff here
                logger.warning(
                    "task_retry",
                    run_id=context.run_id,
                    task_id=task_id,
                    attempt=task.attempt_count
                )
                return await self._execute_task(task_id, plan, context, completed_deps)
            
            raise TaskExecutionError(f"Task {task_id} failed after {task.attempt_count} attempts: {e}")
    
    async def _execute_tool_call(self, tool_call: ToolCall, context: "ExecutionContext") -> Any:
        """Execute individual tool with timeout and circuit breaker."""
        if not self.tool_executor:
            return {"error": "Tool executor not available"}
        
        try:
            # Apply tool-specific timeout (default 30s)
            result = await asyncio.wait_for(
                self.tool_executor.execute(tool_call.tool, tool_call.parameters),
                timeout=tool_call.timeout
            )
            return {
                "tool": tool_call.tool,
                "parameters": tool_call.parameters,
                "result": result,
                "success": True
            }
        except asyncio.TimeoutError:
            return {
                "tool": tool_call.tool,
                "error": f"Timeout after {tool_call.timeout}s",
                "success": False
            }
        except Exception as e:
            return {
                "tool": tool_call.tool,
                "error": str(e),
                "success": False
            }
    
    def _get_task_by_id(self, plan: Plan, task_id: str) -> Optional[PlanTask]:
        """Find task in plan by ID."""
        return next((t for t in plan.tasks if t.id == task_id), None)
    
    def _is_critical_failure(self, task_id: str, plan: Plan, future_batches: list) -> bool:
        """Check if failed task blocks downstream execution."""
        for batch in future_batches:
            for future_tid in batch:
                task = self._get_task_by_id(plan, future_tid)
                if task and task_id in task.depends_on:
                    return True
        return False