# phases/reflect.py
from typing import TYPE_CHECKING, List
import structlog

from ..schemas import ReflectionOutput  # Fixed: was ReflectionResult
from ...model.llm import call_llm, LLMError

if TYPE_CHECKING:
    from orchestrator import ExecutionContext

logger = structlog.get_logger()


class ReflectPhase:
    def __init__(self, model: str):
        self.model = model
    
    async def run(self, context: "ExecutionContext") -> ReflectionOutput:
        """
        Analyze execution quality and determine if we should continue, stop, or replan.
        Uses structured output for consistent decision making.
        """
        system_prompt = """You are a senior financial research auditor. Critically evaluate the work done.

Assessment Criteria:
1. Data Completeness: Do we have all required financial metrics and context?
2. Accuracy: Are calculations correct? Are data sources recent?
3. Relevance: Does the information directly answer the user's query?
4. Confidence: How certain are we about the conclusions?

Output JSON matching ReflectionOutput schema:
- is_complete: Only true if confidence > 0.8 AND all requirements met
- confidence: 0.0-1.0 scale
- reasoning: Detailed critique (2-3 sentences)
- missing_info: Specific gaps that need filling
- needs_replanning: True if the approach is fundamentally wrong
- tasks_to_retry: IDs of specific failed tasks worth retrying
- suggested_next_steps: Specific guidance for next iteration

Be conservative. Only mark complete if truly satisfied."""
        
        execution_summary = self._build_summary(context)
        
        user_prompt = f"""Query: {context.query}
Original Intent: {context.understanding.intent if context.understanding else 'N/A'}
Iteration: {context.iteration + 1}

Execution Summary:
{execution_summary}

Evaluate if we should:
A) STOP - Answer is ready (is_complete=true)
B) CONTINUE - Iterate with improvements (is_complete=false)
C) REPLAN - Current approach is wrong (needs_replanning=true)"""
        
        try:
            reflection = await call_llm(
                prompt=user_prompt,
                system_prompt=system_prompt,
                model=self.model,
                output_model=ReflectionOutput,  # Fixed: was ReflectionResult
                temperature=0.3,  # Lower temp for consistent evaluation
                max_tokens=1500
            )
            
            logger.info(
                "reflection_complete",
                run_id=context.run_id,
                is_complete=reflection.is_complete,
                confidence=reflection.confidence,
                needs_replanning=reflection.needs_replanning,
                missing_count=len(reflection.missing_info)
            )
            
            return reflection
            
        except LLMError as e:
            logger.error("reflection_llm_failed", run_id=context.run_id, error=str(e))
            return self._fallback_reflection(context)
        except Exception as e:
            logger.error("reflection_unexpected_error", run_id=context.run_id, error=str(e))
            return self._fallback_reflection(context)
    
    def _build_summary(self, context: "ExecutionContext") -> str:
        """Build rich execution summary for the LLM."""
        lines = []
        
        # Current plan status
        if context.current_plan:
            lines.append(f"Current Plan: {context.current_plan.summary}")
            lines.append(f"Tasks ({len(context.current_plan.tasks)} total):")
            
            for task in context.current_plan.tasks:
                # Use string comparison for status
                status_icon = {
                    "completed": "✓",
                    "failed": "✗",
                    "running": "⟳",
                    "pending": "○",
                    "skipped": "⊘"
                }.get(task.status, "?")
                
                lines.append(f"  {status_icon} {task.id}: {task.description}")
                
                if task.error:
                    lines.append(f"    Error: {task.error[:100]}")
                
                # Include result preview if available
                if task.id in context.task_results:
                    result = context.task_results[task.id]
                    if isinstance(result, dict) and "tool_results" in result:
                        success_count = sum(
                            1 for r in result["tool_results"] 
                            if r.get("success")
                        )
                        total_count = len(result["tool_results"])
                        lines.append(f"    Results: {success_count}/{total_count} tools succeeded")
        
        # Historical context from previous iterations
        if context.completed_plans:
            lines.append(f"\nPrevious Iterations: {len(context.completed_plans)}")
            total_completed = sum(
                len([t for t in p.tasks if t.status == "completed"])
                for p in context.completed_plans
            )
            lines.append(f"Cumulative tasks completed: {total_completed}")
        
        return "\n".join(lines) if lines else "No execution data available."
    
    def _fallback_reflection(self, context: "ExecutionContext") -> ReflectionOutput:
        """Conservative fallback when reflection fails."""
        # Check if we have any completed tasks
        has_results = bool(context.task_results)
        
        return ReflectionOutput(
            is_complete=False,  # Never assume complete on error
            confidence=0.3 if has_results else 0.0,
            reasoning="Reflection system encountered an error. Proceeding cautiously with available data.",
            missing_info=["Verification of current results"],
            needs_replanning=False,
            tasks_to_retry=[],  # Don't retry blindly on system failure
            suggested_next_steps="Continue with current plan and proceed to answer generation if max iterations reached."
        )