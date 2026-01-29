# phases/answer.py
from typing import TYPE_CHECKING, AsyncGenerator
import structlog

from ...model.llm import call_llm_stream, LLMError

if TYPE_CHECKING:
    from orchestrator import ExecutionContext

logger = structlog.get_logger()


class AnswerPhase:
    def __init__(self, model: str):
        self.model = model
    
    async def run(self, context: "ExecutionContext") -> AsyncGenerator[str, None]:
        """
        Generate final synthesized answer using streaming.
        Yields tokens for real-time display.
        """
        system_prompt = """You are FinancialAgentia, an expert financial analyst synthesizing research findings.

Guidelines:
- Lead with a clear "Summary" section (2-3 sentences)
- Provide "Detailed Analysis" with specific data points and citations
- Include "Risks & Considerations" for balanced perspective
- Cite specific metrics with dates when possible
- If data is incomplete, acknowledge limitations explicitly
- Use professional financial terminology appropriately
- Format with clear markdown headers"""
        
        research_context = self._assemble_context(context)
        
        user_prompt = f"""Original Query: {context.query}

Research Context:
{research_context}

Instruction: Synthesize the above research into a comprehensive financial analysis. Answer based solely on the provided research context. If critical data is missing, note this under "Data Limitations"."""
        
        logger.info("answer_generation_start", run_id=context.run_id, context_length=len(research_context))
        
        full_response = []
        try:
            async for token in call_llm_stream(
                prompt=user_prompt,
                system_prompt=system_prompt,
                model=self.model
            ):
                full_response.append(token)
                yield token
            
            logger.info(
                "answer_generation_complete",
                run_id=context.run_id,
                response_length=len("".join(full_response))
            )
            
        except LLMError as e:
            logger.error("answer_generation_failed", run_id=context.run_id, error=str(e))
            error_msg = f"\n\n[Error generating response: {str(e)}. Retrying with simplified context...]"
            yield error_msg
            
            # Retry with truncated context
            try:
                truncated = research_context[:2000] + "... [truncated]"
                user_prompt_retry = f"Query: {context.query}\n\nResearch Summary:\n{truncated}\n\nProvide a brief answer."
                
                async for token in call_llm_stream(
                    prompt=user_prompt_retry,
                    system_prompt="You are a financial analyst. Provide a concise answer based on the research.",
                    model=self.model
                ):
                    yield token
                    
            except Exception as e2:
                yield f"\n\nUnable to generate answer: {str(e2)}"
    
    def _assemble_context(self, context: "ExecutionContext") -> str:
        """Assemble all research findings into context window."""
        parts = []
        
        # User intent and entities
        if context.understanding:
            parts.append("## User Intent")
            parts.append(context.understanding.intent)
            if context.understanding.entities:
                parts.append("\n### Key Entities")
                for e in context.understanding.entities:
                    norm = f" ({e.normalized})" if e.normalized else ""
                    parts.append(f"- {e.type.value}: {e.value}{norm}")
            parts.append("")
        
        # Execution results from all iterations
        if context.completed_plans:
            parts.append("## Research Execution")
            for plan_idx, plan in enumerate(context.completed_plans, 1):
                parts.append(f"\n### Plan {plan_idx}: {plan.summary}")
                
                for task in plan.tasks:
                    icon = "✓" if task.status == "completed" else "✗"
                    parts.append(f"\n{icon} **{task.id}**: {task.description}")
                    
                    if task.result and "tool_results" in task.result:
                        for tr in task.result["tool_results"]:
                            if tr.get("success"):
                                # Summarize successful tool results
                                result_data = tr.get("result", {})
                                if isinstance(result_data, dict):
                                    # Extract key metrics for display
                                    summary = self._summarize_tool_result(tr["tool"], result_data)
                                    if summary:
                                        parts.append(f"  - {summary}")
                            else:
                                parts.append(f"  - Error: {tr.get('error', 'Unknown error')}")
                    
                    if task.error:
                        parts.append(f"  - Failed: {task.error[:100]}")
        
        # Current iteration if not yet added to completed_plans
        if context.current_plan and context.current_plan not in context.completed_plans:
            parts.append(f"\n### Current Plan: {context.current_plan.summary}")
        
        result = "\n".join(parts)
        
        # Context window management - truncate if too long (rough token estimate)
        if len(result) > 8000:  # ~2000 tokens
            result = result[:8000] + "\n\n[Additional context truncated due to length...]"
        
        return result if result else "No research data available."
    
    def _summarize_tool_result(self, tool_name: str, result: dict) -> str:
        """Extract key information from tool results for answer context."""
        if tool_name == "get_stock_price":
            price = result.get("price")
            change = result.get("change_percent")
            if price:
                return f"Price: ${price} ({change:+}%)" if change else f"Price: ${price}"
        
        elif tool_name == "get_financials":
            metric = result.get("metric")
            value = result.get("value")
            if metric and value:
                return f"{metric}: {value}"
        
        elif tool_name == "search_news":
            count = result.get("article_count", 0)
            sentiment = result.get("sentiment", "neutral")
            return f"News: {count} articles ({sentiment} sentiment)"
        
        return None