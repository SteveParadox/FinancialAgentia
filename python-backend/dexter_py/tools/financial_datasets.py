"""Financial research tools backed by Financial Datasets. No synthetic fallback."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from inspect import signature
import os
import re
import time
from typing import Any
from urllib.parse import urlencode

import httpx

ROOT = "https://api.financialdatasets.ai"
SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.^_-]{0,19}$")
PATHS = {
    "get_price_snapshot": ("/prices/snapshot/", "snapshot"),
    "get_prices": ("/prices/", "prices"),
    "get_company_fundamentals": ("/financial-metrics/snapshot/", "snapshot"),
    "get_financial_metrics": ("/financial-metrics/", "financial_metrics"),
    "get_income_statements": ("/financials/income-statements/", "income_statements"),
    "get_balance_sheets": ("/financials/balance-sheets/", "balance_sheets"),
    "get_cash_flow_statements": ("/financials/cash-flow-statements/", "cash_flow_statements"),
    "get_all_financial_statements": ("/financials/", "financials"),
    "get_news": ("/news/", "news"),
    "get_filings": ("/filings/", "filings"),
}

class FinancialDataError(RuntimeError):
    """Financial provider unavailable or returned unusable financial data."""

def validate_ticker(ticker: str) -> str:
    if not isinstance(ticker, str) or not SYMBOL.fullmatch(ticker):
        raise ValueError("Invalid ticker symbol")
    return ticker.upper()

def validate_date(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Date must be ISO YYYY-MM-DD")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value or parsed > datetime.now(timezone.utc).date():
        raise ValueError("Invalid or future date")
    return value

def validate_limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 100:
        raise ValueError("limit must be 1 to 100")
    return value

class FinancialDatasetsClient:
    def __init__(self, api_key: str, *, http_client: httpx.AsyncClient | None = None,
                 timeout: float = 12):
        if not api_key:
            raise ValueError("FINANCIAL_DATASETS_API_KEY is required")
        if not 0 < timeout <= 60:
            raise ValueError("Financial provider timeout must be >0 and <=60")
        self._owns_client = http_client is None
        self.http = http_client or httpx.AsyncClient(
            base_url=ROOT, timeout=timeout, follow_redirects=False,
            limits=httpx.Limits(max_connections=12, max_keepalive_connections=6),
            headers={"x-api-key": api_key, "Accept": "application/json"},
        )
        self.cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def close(self):
        if self._owns_client:
            await self.http.aclose()

    async def retrieve(self, operation: str, ticker: str, **options: Any):
        if operation not in PATHS:
            raise ValueError("Unknown financial operation")
        ticker = validate_ticker(ticker)
        allowed = {"period", "limit", "start_date", "end_date", "interval",
                   "interval_multiplier", "filing_type"}
        if set(options) - allowed:
            raise ValueError("Unsupported provider options")
        if options.get("period") is not None and options["period"] not in {"annual", "quarterly", "ttm"}:
            raise ValueError("Invalid period")
        if options.get("interval") is not None and options["interval"] not in {"minute", "day", "week", "month", "year"}:
            raise ValueError("Invalid interval")
        if "interval_multiplier" in options:
            number = options["interval_multiplier"]
            if type(number) is not int or not 1 <= number <= 100:
                raise ValueError("Invalid interval multiplier")
        if options.get("filing_type") is not None and options["filing_type"] not in {"10-K", "10-Q", "8-K"}:
            raise ValueError("Invalid filing type")
        if "limit" in options:
            options["limit"] = validate_limit(options["limit"])
        for field in ("start_date", "end_date"):
            if field in options:
                options[field] = validate_date(options[field])
        if options.get("start_date") and options.get("end_date") and options["start_date"] > options["end_date"]:
            raise ValueError("Invalid date range")
        path, field = PATHS[operation]
        params = {"ticker": ticker, **{k: v for k, v in options.items() if v is not None}}
        query = urlencode(sorted(params.items()))
        cache_key = operation + ":" + query
        cached = self.cache.get(cache_key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        source_url = ROOT + path + "?" + query
        for attempt in range(3):
            try:
                response = await self.http.get(ROOT + path, params=params)
                if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                    await asyncio.sleep(0.2 * (2 ** attempt))
                    continue
                response.raise_for_status()
                if len(response.content) > 2_000_000:
                    raise FinancialDataError("Provider response size exceeded limit")
                raw = response.json()
                if not isinstance(raw, dict) or not raw.get(field):
                    raise FinancialDataError("Provider returned no usable " + field)
                payload = raw[field]
                data_time = None
                if isinstance(payload, dict):
                    data_time = payload.get("time") or payload.get("date") or payload.get("report_period")
                result = {"ticker": ticker,
                          "provider": "financialdatasets.ai", "source_url": source_url,
                          "retrieved_at": datetime.now(timezone.utc).isoformat(),
                          "data_timestamp": data_time, "data": payload}
                if len(self.cache) >= 128:
                    self.cache.pop(next(iter(self.cache)))
                ttl = 10 if operation == "get_price_snapshot" else 30 if operation == "get_news" else 120
                self.cache[cache_key] = (time.monotonic() + ttl, result)
                return result
            except httpx.HTTPStatusError as exc:
                raise FinancialDataError("Financial provider HTTP " + str(exc.response.status_code)) from exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt == 2:
                    raise FinancialDataError("Financial provider unavailable") from exc
                await asyncio.sleep(0.2 * (2 ** attempt))
        raise FinancialDataError("Financial provider failed")

    async def indicators(self, ticker: str, start_date: str, end_date: str, window: int = 14):
        validate_limit(window)
        research = await self.retrieve("get_prices", ticker, start_date=start_date,
                                       end_date=end_date, interval="day", interval_multiplier=1)
        rows = research["data"]
        if not isinstance(rows, list):
            raise FinancialDataError("Invalid price-history format")
        rows = sorted((r for r in rows if isinstance(r, dict)),
                      key=lambda r: str(r.get("time") or r.get("date") or ""))
        try:
            closes = [Decimal(str(r["close"])) for r in rows]
        except (KeyError, InvalidOperation, ValueError) as exc:
            raise FinancialDataError("Price history contains invalid closes") from exc
        if len(closes) < window + 1 or any(not c.is_finite() or c <= 0 for c in closes):
            raise FinancialDataError("Insufficient valid historical closes")
        gains = [max(closes[i] - closes[i-1], Decimal(0)) for i in range(len(closes)-window, len(closes))]
        losses = [max(closes[i-1] - closes[i], Decimal(0)) for i in range(len(closes)-window, len(closes))]
        gain, loss = sum(gains), sum(losses)
        rsi = (Decimal(100) if loss == 0 and gain > 0
               else Decimal(50) if loss == 0
               else Decimal(100) - Decimal(100) / (1 + gain/loss))
        return {**research, "data": {"sma": str(sum(closes[-window:])/window),
                                     "rsi": str(rsi), "window": window},
                "calculation": "SMA and simple-window RSI from provider close prices"}

def register_financial_tools(executor: Any, api_key: str | None = None):
    """Register genuine source-backed Python tools; return owned provider for shutdown."""
    key = api_key if api_key is not None else os.getenv("FINANCIAL_DATASETS_API_KEY")
    if not key:
        return None
    provider = FinancialDatasetsClient(key, timeout=float(os.getenv("FINANCIAL_DATA_TIMEOUT_SECONDS", "12")))

    async def get_price_snapshot(ticker: str):
        return await provider.retrieve("get_price_snapshot", ticker)
    async def get_prices(ticker: str, start_date: str, end_date: str,
                         interval: str = "day", interval_multiplier: int = 1):
        return await provider.retrieve("get_prices", ticker, start_date=start_date,
                                       end_date=end_date, interval=interval,
                                       interval_multiplier=interval_multiplier)
    async def get_company_fundamentals(ticker: str):
        return await provider.retrieve("get_company_fundamentals", ticker)
    async def get_financial_metrics(ticker: str, period: str = "ttm", limit: int = 4):
        return await provider.retrieve("get_financial_metrics", ticker, period=period, limit=limit)
    async def get_income_statements(ticker: str, period: str = "annual", limit: int = 4):
        return await provider.retrieve("get_income_statements", ticker, period=period, limit=limit)
    async def get_balance_sheets(ticker: str, period: str = "annual", limit: int = 4):
        return await provider.retrieve("get_balance_sheets", ticker, period=period, limit=limit)
    async def get_cash_flow_statements(ticker: str, period: str = "annual", limit: int = 4):
        return await provider.retrieve("get_cash_flow_statements", ticker, period=period, limit=limit)
    async def get_all_financial_statements(ticker: str, period: str = "annual", limit: int = 4):
        return await provider.retrieve("get_all_financial_statements", ticker, period=period, limit=limit)
    async def get_news(ticker: str, limit: int = 10, start_date: str | None = None,
                       end_date: str | None = None):
        return await provider.retrieve("get_news", ticker, limit=limit,
                                       start_date=start_date, end_date=end_date)
    async def get_filings(ticker: str, limit: int = 10, filing_type: str | None = None):
        return await provider.retrieve("get_filings", ticker, limit=limit, filing_type=filing_type)
    async def calculate_technical_indicators(ticker: str, start_date: str,
                                              end_date: str, window: int = 14):
        return await provider.indicators(ticker, start_date, end_date, window)

    executor.tool_signatures = {}
    for func in (get_price_snapshot, get_prices, get_company_fundamentals,
                 get_financial_metrics, get_income_statements, get_balance_sheets,
                 get_cash_flow_statements, get_all_financial_statements,
                 get_news, get_filings, calculate_technical_indicators):
        executor.register(func.__name__, func)
        executor.tool_signatures[func.__name__] = str(signature(func))
    return provider
