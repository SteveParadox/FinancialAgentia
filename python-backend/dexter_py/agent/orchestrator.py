# orchestrator.py
import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import uuid
from datetime import datetime
import structlog

from .schemas import Plan, Understanding, ReflectionOutput, PlanTask, TaskType
from ..model.llm_integration import StructuredLLMClient
from ..exceptions import OrchestratorError,  MaxIterationsError

logger = structlog.get_logger()

class StopReason(Enum):
    COMPLETE = "reflection_complete"
    MAX_ITERATIONS = "max_iterations"
    NO_PROGRESS = "no_progress"
    HIGH_CONFIDENCE = "high_confidence"
    ERROR = "error"

@dataclass
class ExecutionContext:
    """Isolated context for a single run"""
    run_id: str
    query: str
    model: str
    understanding: Optional[Understanding] = None
    current_plan: Optional[Plan] = None
    completed_plans: List[Plan] = field(default_factory=list)
    task_results: Dict[str, Any] = field(default_factory=dict)
    iteration: int = 0
    stop_reason: Optional[StopReason] = None
    
    def get_retryable_tasks(self) -> List[str]:
        """Get IDs of tasks that failed but can be retried"""
        if not self.current_plan:
            return []
        return [
            t.id for t in self.current_plan.tasks 
            if t.status == "failed" and t.max_retries > 0
        ]

class Orchestrator:
    """
    Financial AI Agent Orchestrator with proper LLM integration
    """
    
    PHASE_TIMEOUTS = {
        'understand': 30,
        'plan': 45,
        'execute': 120,
        'reflect': 30,
        'answer': 60
    }
    
    def __init__(
        self,
        model: str,
        max_iterations: int = 3,
        llm_client: Optional[StructuredLLMClient] = None
    ):
        self.model = model
        self.max_iterations = max_iterations
        self.llm = llm_client or StructuredLLMClient(model)
        self.tool_executor = None  # Set via configure()
        
    def configure_tools(self, tool_executor):
        """Inject tool executor dependency"""
        self.tool_executor = tool_executor
        
    async def run(self, query: str) -> str:
        """
        Main execution flow
        """
        run_id = str(uuid.uuid4())
        context = ExecutionContext(run_id=run_id, query=query, model=self.model)
        
        logger.info("orchestrator_start", run_id=run_id, query=query[:100])
        
        try:
            # Phase 1: Understanding
            context.understanding = await self._understand(context)
            logger.info("understanding_complete", intent=context.understanding.intent)
            
            # Iterative refinement loop
            while context.iteration < self.max_iterations:
                context.iteration += 1
                logger.info(f"iteration_{context.iteration}_start", run_id=run_id)
                
                # Phase 2: Planning
                context.current_plan = await self._plan(context)
                
                # Phase 3: Execution
                await self._execute(context)
                
                context.completed_plans.append(context.current_plan)
                
                # Phase 4: Reflection
                reflection = await self._reflect(context)
                
                # Check stopping conditions
                if reflection.is_complete and reflection.confidence > 0.8:
                    context.stop_reason = StopReason.COMPLETE
                    break
                    
                if reflection.confidence > 0.95:
                    context.stop_reason = StopReason.HIGH_CONFIDENCE
                    break
                
                # Check for progress
                if context.iteration > 1 and not reflection.tasks_to_retry:
                    completed_tasks = sum(
                        1 for t in context.current_plan.tasks 
                        if t.status == "completed"
                    )
                    if completed_tasks == 0:
                        context.stop_reason = StopReason.NO_PROGRESS
                        break
            
            else:
                context.stop_reason = StopReason.MAX_ITERATIONS
            
            # Phase 5: Answer
            answer = await self._answer(context)
            return answer
            
        except Exception as e:
            logger.error("orchestrator_error", run_id=run_id, error=str(e))
            context.stop_reason = StopReason.ERROR
            return f"I encountered an error analyzing your request: {str(e)}"
        
        finally:
            logger.info(
                "orchestrator_end", 
                run_id=run_id, 
                iterations=context.iteration,
                stop_reason=context.stop_reason.value if context.stop_reason else None
            )
    
    async def _understand(self, context: ExecutionContext) -> Understanding:
        """Understanding phase using structured output"""
        system_prompt = """You are a financial query analyzer. Extract the user's intent and entities.
        
Rules:
- Normalize company names to ticker symbols (Apple -> AAPL)
- Identify time periods and specific metrics
- Assess complexity (0.1=simple query, 0.9=complex multi-step analysis)"""
        
        user_prompt = f"Query: {context.query}"
        
        try:
            return await self.llm.complete_structured(
                prompt=user_prompt,
                output_model=Understanding,
                system_prompt=system_prompt,
                temperature=0.1
            )
        except Exception as e:
            logger.error("understand_failed", error=str(e))
            # Graceful fallback
            return Understanding(intent=context.query, entities=[])
    
    async def _plan(self, context: ExecutionContext) -> Plan:
        """Planning phase with context from previous iterations"""
        iteration = context.iteration
        
        system_prompt = f"""You are a financial research planner. Create a structured plan.

Current date: {datetime.now().isoformat()}
Iteration: {iteration}

Guidelines:
- Break complex queries into specific, verifiable tasks
- Use task IDs like "1", "2", "3" (will be prefixed automatically)
- Specify dependencies in depends_on (refer to task IDs)
- Include validation tasks
- Prefer parallel execution when possible

If this is iteration > 1, focus on:
- Missing information from previous attempts
- Tasks that failed and need retry
- New angles suggested by reflection"""
        
        # Build context from previous attempts
        prior_context = ""
        if context.completed_plans:
            prior_context = "\n\nPrevious attempts:\n"
            for i, plan in enumerate(context.completed_plans[-2:], 1):
                prior_context += f"Attempt {i}: {plan.summary}\n"
                for task in plan.tasks:
                    status = "✓" if task.status == "completed" else "✗"
                    prior_context += f"  {status} {task.id}: {task.description}\n"
        
        user_prompt = f"""User Query: {context.query}
Intent: {context.understanding.intent if context.understanding else 'N/A'}
Entities: {[e.value for e in context.understanding.entities] if context.understanding else []}
{prior_context}

Create a focused research plan."""
        
        try:
            plan = await self.llm.complete_structured(
                prompt=user_prompt,
                output_model=Plan,
                system_prompt=system_prefix,
                temperature=0.2
            )
            
            # Normalize task IDs with iteration prefix
            for task in plan.tasks:
                task.id = f"iter{iteration}_{task.id}"
                task.depends_on = [
                    f"iter{iteration}_{d}" if not d.startswith(f"iter{iteration}_") else d
                    for d in task.depends_on
                ]
            
            return plan
            
        except Exception as e:
            logger.error("plan_failed", error=str(e))
            # Fallback plan
            return Plan(
                summary="Direct research plan",
                tasks=[PlanTask(
                    id=f"iter{iteration}_fallback",
                    description=f"Research: {context.query}",
                    task_type=TaskType.RESEARCH
                )]
            )
    
    async def _execute(self, context: ExecutionContext):
        """Execute tasks with topological ordering and parallelism"""
        if not context.current_plan or not self.tool_executor:
            return
        
        plan = context.current_plan
        batches = plan.get_execution_order()
        
        for batch_idx, batch in enumerate(batches):
            logger.info(f"executing_batch_{batch_idx}", task_count=len(batch))
            
            # Execute batch in parallel with semaphore
            semaphore = asyncio.Semaphore(5)  # Max 5 concurrent tasks
            
            async def run_task(task_id: str):
                async with semaphore:
                    task = next((t for t in plan.tasks if t.id == task_id), None)
                    if not task:
                        return
                    
                    # Check dependencies
                    incomplete_deps = [
                        dep for dep in task.depends_on 
                        if dep not in context.task_results or context.task_results[dep].get("error")
                    ]
                    if incomplete_deps:
                        task.status = "skipped"
                        task.error = f"Missing dependencies: {incomplete_deps}"
                        return
                    
                    task.start_time = datetime.now()
                    task.status = "running"
                    
                    try:
                        # Execute tool calls
                        results = []
                        for tool_call in task.tool_calls:
                            result = await self._execute_tool_call(tool_call)
                            results.append(result)
                        
                        task.status = "completed"
                        task.result = {"tool_results": results}
                        context.task_results[task.id] = task.result
                        
                    except Exception as e:
                        task.status = "failed"
                        task.error = str(e)
                        task.max_retries -= 1
                        context.task_results[task.id] = {"error": str(e)}
                    
                    task.end_time = datetime.now()
            
            await asyncio.gather(*[run_task(tid) for tid in batch])
    
    async def _execute_tool_call(self, tool_call) -> Any:
        """Execute single tool with error handling"""
        if not self.tool_executor:
            return {"error": "Tool executor not configured"}
        
        try:
            # Timeout handling
            result = await asyncio.wait_for(
                self.tool_executor.execute(
                    tool_call.tool,
                    tool_call.parameters
                ),
                timeout=tool_call.timeout
            )
            return result
        except asyncio.TimeoutError:
            return {"error": f"Tool {tool_call.tool} timed out after {tool_call.timeout}s"}
        except Exception as e:
            return {"error": f"Tool execution failed: {str(e)}"}
    
    async def _reflect(self, context: ExecutionContext) -> ReflectionOutput:
        """Reflection phase to assess progress"""
        system_prompt = """You are a research auditor. Analyze the execution results and decide:
1. Is the information sufficient to answer the query?
2. What's missing or incorrect?
3. Should we continue or stop?

Be critical. Only mark complete if you have high confidence (>0.8) in the answer."""
        
        # Build execution summary
        summary = f"Query: {context.query}\n\nExecution History:\n"
        for plan in context.completed_plans:
            summary += f"\nPlan: {plan.summary}\n"
            for task in plan.tasks:
                icon = "✓" if task.status == "completed" else "✗" if task.status == "failed" else "○"
                summary += f"  {icon} {task.id}: {task.description}\n"
                if task.error:
                    summary += f"    Error: {task.error}\n"
        
        user_prompt = f"{summary}\n\nAssess completeness and suggest next steps."
        
        try:
            return await self.llm.complete_structured(
                prompt=user_prompt,
                output_model=ReflectionOutput,
                system_prompt=system_prompt,
                temperature=0.3
            )
        except Exception as e:
            logger.error("reflection_failed", error=str(e))
            return ReflectionOutput(
                is_complete=False,
                confidence=0.0,
                reasoning="Reflection failed, continuing cautiously",
                needs_replanning=False
            )
    
    async def _answer(self, context: ExecutionContext) -> str:
        """Generate final answer with streaming"""
        # Assemble research context
        research_context = []
        for plan in context.completed_plans:
            research_context.append(f"Plan: {plan.summary}")
            for task in plan.tasks:
                if task.status == "completed" and task.result:
                    research_context.append(f"  {task.id}: {task.result}")
        
        system_prompt = """You are FinancialAgentia, an expert financial analyst.
Synthesize the research results into a comprehensive answer.

Guidelines:
- Cite specific data points
- Acknowledge uncertainties
- Structure with Summary, Details, and Risks/Considerations"""
        
        user_prompt = f"""User Query: {context.query}

Research Results:
{chr(10).join(research_context)}

Provide a comprehensive answer."""
        
        # Collect stream
        chunks = []
        async for token in self.llm.stream_text(
            prompt=user_prompt,
            system_prompt=system_prompt
        ):
            chunks.append(token)
        
        return "".join(chunks)