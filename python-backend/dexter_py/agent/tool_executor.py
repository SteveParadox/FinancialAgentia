"""Bounded, audited financial tool execution with asyncio-safe circuit breakers."""
import asyncio
from inspect import signature
import time
from typing import Dict, Any, Callable

import structlog

logger = structlog.get_logger()


class ToolCircuitOpen(RuntimeError):
    """Tool has repeatedly failed and is temporarily unavailable."""


class ToolExecutor:
    """Owns tool registration, argument preflight and async/sync breaker state."""

    def __init__(self):
        self.tools: Dict[str, Callable] = {}
        self._state: Dict[str, dict] = {}

    def register(self, name: str, func: Callable, fail_max: int = 5):
        if not name or not callable(func) or type(fail_max) is not int or fail_max < 1:
            raise ValueError("Invalid tool registration")
        self.tools[name] = func
        self._state[name] = {
            "fail_max": fail_max,
            "failures": 0,
            "opened_until": 0.0,
            "trial": False,
        }

    async def execute(self, tool_name: str, parameters: Dict[str, Any]) -> Any:
        if tool_name not in self.tools:
            raise ValueError(f"Unknown tool: {tool_name}")
        func = self.tools[tool_name]
        if not isinstance(parameters, dict):
            raise ValueError("Tool parameters must be an object")
        try:
            signature(func).bind(**parameters)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid arguments for tool {tool_name}") from exc

        state = self._state[tool_name]
        now = time.monotonic()
        if state["opened_until"]:
            if now < state["opened_until"] or state["trial"]:
                raise ToolCircuitOpen(f"Tool {tool_name} is temporarily unavailable")
            state["trial"] = True
        logger.info("tool_execution", tool=tool_name, params=list(parameters.keys()))
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(**parameters)
            else:
                result = await asyncio.to_thread(func, **parameters)
        except asyncio.CancelledError:
            if state["trial"]:
                state["trial"] = False
                state["opened_until"] = time.monotonic() + 30
            raise
        except Exception as exc:
            state["failures"] += 1
            if state["failures"] >= state["fail_max"]:
                state["opened_until"] = time.monotonic() + 30
                state["trial"] = False
            logger.error("tool_failed", tool=tool_name, error_type=type(exc).__name__)
            raise
        else:
            state["failures"] = 0
            state["opened_until"] = 0.0
            state["trial"] = False
            return result


async def get_stock_price(ticker: str) -> Dict:
    """Legacy non-production example, not registered."""
    raise RuntimeError("Stock price provider is not integrated in the Python runtime")


def calculate_metrics(data: Dict, metric_type: str) -> Dict:
    """Legacy helper kept for compatibility, not registered."""
    if metric_type == "pe_ratio":
        price = data.get("price", 0)
        earnings = data.get("eps", 1)
        return {"pe_ratio": price / earnings if earnings else None}
    return {}
