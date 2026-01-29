# phases/understand.py
import re
from typing import TYPE_CHECKING, List, Optional
import structlog

from ..schemas import Understanding, Entity, EntityType
from ...model.llm import call_llm, LLMError

if TYPE_CHECKING:
    from orchestrator import ExecutionContext

logger = structlog.get_logger()


class UnderstandPhase:
    def __init__(self, model: str):
        self.model = model
    
    async def run(self, context: "ExecutionContext") -> Understanding:
        """
        Extract intent and entities using structured LLM output.
        Falls back to heuristic extraction on failure.
        """
        system_prompt = """You are a financial query analyzer. Extract structured information from user queries.
        
Rules:
- intent: Clear statement of what user wants (max 20 words)
- entities: Array of objects with type (ticker/company/date/metric/sector) and value
- complexity_score: 0.1 for simple price checks, 0.9 for multi-step analysis
- temporal_constraints: Any date ranges mentioned (ISO format preferred)
- required_data_sources: Which APIs needed (yahoo_finance, sec_filings, news, etc.)

Normalize company names to tickers when possible (e.g., "Apple" -> ticker "AAPL")."""
        
        user_prompt = f"Query: {context.query}"
        
        try:
            # Use native structured output from llm.py
            understanding = await call_llm(
                prompt=user_prompt,
                system_prompt=system_prompt,
                model=self.model,
                output_model=Understanding,
                temperature=0.1,
                max_tokens=1000
            )
            
            # Post-process entities for normalization
            for entity in understanding.entities:
                if entity.type == EntityType.COMPANY and not entity.normalized:
                    ticker = self._company_to_ticker(entity.value)
                    if ticker:
                        entity.normalized = ticker
                        entity.type = EntityType.TICKER
            
            logger.info(
                "understanding_complete",
                run_id=context.run_id,
                intent=understanding.intent,
                entities_count=len(understanding.entities),
                complexity=understanding.complexity_score
            )
            
            return understanding
            
        except LLMError as e:
            logger.error("llm_understanding_failed", run_id=context.run_id, error=str(e))
            # Graceful degradation to heuristic extraction
            return self._fallback_understanding(context.query)
        except Exception as e:
            logger.error("understanding_unexpected_error", run_id=context.run_id, error=str(e))
            return Understanding(
                intent=context.query,
                entities=self._extract_entities_heuristic(context.query),
                complexity_score=0.5
            )
    
    def _company_to_ticker(self, name: str) -> Optional[str]:
        """Simple company-to-ticker mapping. Replace with API lookup for production."""
        mapping = {
            'apple': 'AAPL', 'microsoft': 'MSFT', 'amazon': 'AMZN',
            'google': 'GOOGL', 'alphabet': 'GOOGL', 'tesla': 'TSLA',
            'meta': 'META', 'facebook': 'META', 'nvidia': 'NVDA',
            'berkshire hathaway': 'BRK.B', 'berkshire': 'BRK.B',
            'jpmorgan': 'JPM', 'jp morgan': 'JPM',
            'visa': 'V', 'mastercard': 'MA', 'johnson & johnson': 'JNJ'
        }
        return mapping.get(name.lower().strip())
    
    def _extract_entities_heuristic(self, query: str) -> List[Entity]:
        """Fallback regex extraction when LLM fails."""
        entities = []
        
        # Tickers (uppercase 1-5 chars, word boundaries)
        for match in re.finditer(r'\b([A-Z]{1,5})\b', query):
            val = match.group(1)
            # Filter out common words that look like tickers
            if val not in ['A', 'I', 'AN', 'THE', 'CEO', 'CFO']:
                entities.append(Entity(type=EntityType.TICKER, value=val))
        
        # Companies with common suffixes
        pattern = r'\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*\s+(?:Inc|Corp|Ltd|Company|Co\.))\b'
        for match in re.finditer(pattern, query):
            entities.append(Entity(type=EntityType.COMPANY, value=match.group(1)))
        
        return entities
    
    def _fallback_understanding(self, query: str) -> Understanding:
        """Complete fallback when everything fails."""
        return Understanding(
            intent=f"Research and analyze: {query}",
            entities=self._extract_entities_heuristic(query),
            complexity_score=0.5,
            required_data_sources=["yahoo_finance"]
        )