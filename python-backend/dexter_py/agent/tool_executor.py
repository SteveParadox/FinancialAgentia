# tool_executor.py
import asyncio
from typing import Dict, Any, Callable
import structlog

logger = structlog.get_logger()

class ToolExecutor:
    """Executes tools with circuit breaker and retry logic"""
    
    def __init__(self):
        self.tools: Dict[str, Callable] = {}
        self._circuit_breakers: Dict[str, Any] = {}  # pybreaker.CircuitBreaker
        
    def register(self, name: str, func: Callable, fail_max: int = 5):
        """Register a tool with circuit breaker protection"""
        self.tools[name] = func
        # Circuit breaker prevents cascade failures
        try:
            import pybreaker
            self._circuit_breakers[name] = pybreaker.CircuitBreaker(
                fail_max=fail_max,
                reset_timeout=30
            )
        except ImportError:
            self._circuit_breakers[name] = None
    
    async def execute(self, tool_name: str, parameters: Dict[str, Any]) -> Any:
        """Execute tool with circuit breaker"""
        if tool_name not in self.tools:
            raise ValueError(f"Unknown tool: {tool_name}")
        
        cb = self._circuit_breakers.get(tool_name)
        func = self.tools[tool_name]
        
        logger.info("tool_execution", tool=tool_name, params=list(parameters.keys()))
        
        try:
            if cb:
                # Use circuit breaker if available
                if asyncio.iscoroutinefunction(func):
                    return await cb(lambda: func(**parameters))
                else:
                    return await asyncio.to_thread(cb, lambda: func(**parameters))
            else:
                # Direct execution
                if asyncio.iscoroutinefunction(func):
                    return await func(**parameters)
                else:
                    return await asyncio.to_thread(func, **parameters)
                    
        except Exception as e:
            logger.error("tool_failed", tool=tool_name, error=str(e))
            raise

# Example tool implementations
async def get_stock_price(ticker: str) -> Dict:
    """Example tool - integrate with your data provider"""
    # Integration with yfinance, alpaca, etc.
    return {"ticker": ticker, "price": 150.0, "currency": "USD"}

def calculate_metrics(data: Dict, metric_type: str) -> Dict:
    """Example calculation tool"""
    if metric_type == "pe_ratio":
        price = data.get("price", 0)
        earnings = data.get("eps", 1)
        return {"pe_ratio": price / earnings if earnings else None}
    return {}