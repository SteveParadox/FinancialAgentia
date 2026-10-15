"""Financial research tools backed by Financial Datasets. No synthetic fallback."""
from __future__ import annotations

import asyncio
import copy
import random
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from inspect import signature
import os
import re
import time
from weakref import WeakValueDictionary
from typing import Any
from urllib.parse import urlencode

import httpx

ROOT = "https://api.financialdatasets.ai"
SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.^_-]{0,19}$")
from .calculations import analyze_prices

PATHS = {
    "get_price_snapshot": ("/prices/snapshot/", "snapshot"),
    "get_prices": ("/prices/", "prices"),
    "get_company_fundamentals": ("/financial-metrics/snapshot/", "snapshot"),
    "get_company_profile": ("/company/facts/", "company_facts"),
    "get_financial_metrics": ("/financial-metrics/", "financial_metrics"),
    "get_income_statements": ("/financials/income-statements/", "income_statements"),
    "get_balance_sheets": ("/financials/balance-sheets/", "balance_sheets"),
    "get_cash_flow_statements": ("/financials/cash-flow-statements/", "cash_flow_statements"),
    "get_all_financial_statements": ("/financials/", "financials"),
    "get_news": ("/news/", "news"),
    "get_filings": ("/filings/", "filings"),
}

class FinancialDataError(RuntimeError):
    """Permanent invalid, unavailable or unusable financial source response."""
    retryable = False


class FinancialDataTransportError(FinancialDataError):
    """Transient provider outage after bounded HTTP retries."""
    retryable = True

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

def observation_date(value: Any) -> date:
    if not isinstance(value, str):
        raise FinancialDataError("Missing observation timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FinancialDataError("Invalid observation timestamp") from exc
    if parsed.tzinfo is None and len(value) > 10:
        raise FinancialDataError("Observation timestamp lacks timezone")
    if parsed.tzinfo is not None:
        future = parsed > datetime.now(timezone.utc)
    else:
        future = parsed.date() > datetime.now(timezone.utc).date()
    if future:
        raise FinancialDataError("Future observation rejected")
    return parsed.date()


def validate_payload(operation: str, payload: Any, ticker: str) -> None:
    """Reject masquerading or structurally invalid financial data before agent access."""
    if isinstance(payload, dict):
        reported = payload.get("ticker")
        if reported is not None and (not isinstance(reported, str) or
                                     reported.upper() != ticker):
            raise FinancialDataError("Provider returned a different ticker")
    elif isinstance(payload, list):
        if not payload or not all(isinstance(row, dict) for row in payload):
            raise FinancialDataError("Provider returned malformed data records")
        for row in payload:
            reported = row.get("ticker")
            if reported is not None and (not isinstance(reported, str) or
                                         reported.upper() != ticker):
                raise FinancialDataError("Provider returned a different ticker")
    else:
        raise FinancialDataError("Provider returned an invalid data structure")
    # Combined financials contain records under statement-specific arrays.
    rows = payload if isinstance(payload, list) else [payload]
    if operation == "get_all_financial_statements":
        rows = []
        for name in ("income_statements", "balance_sheets", "cash_flow_statements"):
            nested = payload.get(name, []) if isinstance(payload, dict) else []
            if not isinstance(nested, list) or any(not isinstance(r, dict) for r in nested):
                raise FinancialDataError("Malformed combined financial statements")
            rows.extend(nested)
    for row in rows:
        if row.get("ticker", ticker) != ticker:
            raise FinancialDataError("Provider returned a different ticker")
        for field in ("time", "date", "report_period", "published_at"):
            if row.get(field) is not None:
                observation_date(row[field])
        if row.get("currency") is not None and not re.fullmatch(r"[A-Z]{3}", str(row["currency"])):
            raise FinancialDataError("Invalid currency")
    if operation == "get_prices":
        if not isinstance(payload, list):
            raise FinancialDataError("Historical prices must be records")
        dates = []
        for row in payload:
            if "close" not in row:
                raise FinancialDataError("Historical price row lacks close")
            try:
                close = Decimal(str(row["close"]))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise FinancialDataError("Historical price contains invalid close") from exc
            if isinstance(row["close"], bool) or not close.is_finite() or close <= 0:
                raise FinancialDataError("Historical price must be finite and positive")
            dates.append(str(row.get("time") or row.get("date") or ""))
            observation_date(dates[-1])
        currencies = {row["currency"] for row in payload if row.get("currency")}
        if len(currencies) > 1:
            raise FinancialDataError("Mixed currencies in historical prices")
        if len(set(dates)) != len(dates):
            raise FinancialDataError("Duplicate historical timestamps")
    if operation == "get_price_snapshot":
        if not isinstance(payload, dict) or "price" not in payload:
            raise FinancialDataError("Quote is missing its price")
        price = payload["price"]
        try:
            if isinstance(price, bool):
                raise InvalidOperation
            value = Decimal(str(price))
        except (InvalidOperation, ValueError, TypeError):
            raise FinancialDataError("Quote has an invalid price") from None
        if not value.is_finite() or value <= 0:
            raise FinancialDataError("Quote price must be positive and finite")
    if operation == "get_all_financial_statements":
        if not isinstance(payload, dict) or not any(
            isinstance(payload.get(key), list) and payload[key]
            for key in ("income_statements", "balance_sheets", "cash_flow_statements")
        ):
            raise FinancialDataError("Provider returned invalid combined financial statements")


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
            proxy=os.getenv("HTTPS_PROXY") or os.getenv("https_proxy"),
            limits=httpx.Limits(max_connections=12, max_keepalive_connections=6),
            headers={"x-api-key": api_key, "Accept": "application/json"},
        )
        self.cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._requests = WeakValueDictionary()

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
        if operation == "get_news" and options.get("limit", 5) > 10:
            raise ValueError("Company news limit must be 1 to 10")
        for field in ("start_date", "end_date"):
            if field in options:
                options[field] = validate_date(options[field])
        if options.get("start_date") and options.get("end_date") and options["start_date"] > options["end_date"]:
            raise ValueError("Invalid date range")
        path, field = PATHS[operation]
        params = {"ticker": ticker, **{k: v for k, v in options.items() if v is not None}}
        if operation == "get_news":
            params = {k: v for k, v in params.items() if k not in {"start_date", "end_date"}}
        query = urlencode(sorted(params.items()))
        cache_key = operation + ":" + query + ":" + str(sorted(options.items()))
        # Coalesce concurrent identical provider requests without retaining
        # unbounded lock keys. Recheck the validated cache inside the lock.
        lock = self._requests.setdefault(cache_key, asyncio.Lock())
        async with lock:
            cached = self.cache.get(cache_key)
            if cached and cached[0] > time.monotonic():
                result = copy.deepcopy(cached[1])
                result["cache_status"] = "cached"
                return result
            source_url = ROOT + path + "?" + query
            for attempt in range(3):
                try:
                    response = await self.http.get(ROOT + path, params=params)
                    if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                        retry_after = response.headers.get("retry-after", "")
                        try:
                            delay = max(0.0, min(float(retry_after), 3.0)) if retry_after else 0.2 * (2 ** attempt)
                        except ValueError:
                            delay = 0.2 * (2 ** attempt)
                        await asyncio.sleep(delay + random.uniform(0, 0.05))
                        continue
                    response.raise_for_status()
                    if len(response.content) > 2_000_000:
                        raise FinancialDataError("Provider response size exceeded limit")
                    try:
                        raw = response.json()
                    except ValueError as exc:
                        raise FinancialDataError("Invalid JSON from financial provider") from exc
                    if not isinstance(raw, dict) or not raw.get(field):
                        raise FinancialDataError("Provider returned no usable " + field)
                    payload = raw[field]
                    validate_payload(operation, payload, ticker)
                    if operation == "get_news" and (options.get("start_date") or options.get("end_date")):
                        payload = [row for row in payload if row.get("date") and
                                   (not options.get("start_date") or row["date"][:10] >= options["start_date"]) and
                                   (not options.get("end_date") or row["date"][:10] <= options["end_date"])]
                        if not payload:
                            raise FinancialDataError("No news in requested interval; historical coverage is not guaranteed")
                    if operation == "get_prices":
                        for row in payload:
                            day = observation_date(row.get("time") or row.get("date")).isoformat()
                            if (options.get("start_date") and day < options["start_date"]) or (options.get("end_date") and day > options["end_date"]):
                                raise FinancialDataError("Price observation outside requested interval")
                    data_time = None
                    if isinstance(payload, dict):
                        data_time = payload.get("time") or payload.get("date") or payload.get("report_period")
                        if operation == "get_all_financial_statements":
                            income = payload.get("income_statements") or []
                            if income and isinstance(income[0], dict):
                                data_time = income[0].get("report_period")
                    elif isinstance(payload, list) and payload:
                        data_time = (payload[0].get("time") or payload[0].get("date")
                                     or payload[0].get("report_period"))
                    missing_statements = []
                    if operation == "get_all_financial_statements":
                        missing_statements = [
                            key for key in ("income_statements", "balance_sheets", "cash_flow_statements")
                            if not payload.get(key)
                        ]
                    result = {"ticker": ticker,
                              "provider": "financialdatasets.ai", "source_url": source_url,
                              "retrieved_at": datetime.now(timezone.utc).isoformat(),
                              "data_timestamp": data_time, "data": payload,
                              "completeness": "partial" if missing_statements or raw.get("next_page_url") else "complete",
                              "has_more": bool(raw.get("next_page_url")),
                              "missing_statements": missing_statements,
                              "cache_status": "fresh", "exchange": None,
                              "market": "US equities" if operation.startswith("get_price") else None,
                              "timezone": "UTC" if data_time and (str(data_time).endswith("Z") or "+" in str(data_time)) else None,
                              "adjustment_status": "unspecified",
                              "price_status": "historical" if operation == "get_prices" else "provider_snapshot"}
                    if len(self.cache) >= 128:
                        self.cache.pop(next(iter(self.cache)))
                    ttl = 10 if operation == "get_price_snapshot" else 30 if operation == "get_news" else 120
                    self.cache[cache_key] = (time.monotonic() + ttl, copy.deepcopy(result))
                    return result
                except httpx.HTTPStatusError as exc:
                    error = (FinancialDataTransportError if exc.response.status_code in
                             (429, 500, 502, 503, 504) else FinancialDataError)
                    raise error("Financial provider HTTP " + str(exc.response.status_code)) from exc
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt == 2:
                        raise FinancialDataTransportError("Financial provider unavailable") from exc
                    await asyncio.sleep(0.2 * (2 ** attempt) + random.uniform(0, 0.05))
            raise FinancialDataError("Financial provider failed")


    async def financial_ratios(self, ticker: str, period: str = "annual"):
        """Derive ratios from matching actual financial-statement reporting periods."""
        research = await self.retrieve("get_all_financial_statements", ticker,
                                       period=period, limit=1)
        statements = research["data"]
        if not isinstance(statements, dict):
            raise FinancialDataError("Financial statements must be structured")
        income_rows = statements.get("income_statements")
        balance_rows = statements.get("balance_sheets")
        if not isinstance(income_rows, list) or not income_rows or not isinstance(balance_rows, list) or not balance_rows:
            raise FinancialDataError("Income and balance statements are required")
        income, balance = income_rows[0], balance_rows[0]
        if not isinstance(income, dict) or not isinstance(balance, dict):
            raise FinancialDataError("Malformed financial statement entries")
        period_end = income.get("report_period")
        if (not period_end or not income.get("currency") or
                period_end != balance.get("report_period") or
                income.get("currency") != balance.get("currency") or
                (income.get("period") and balance.get("period") and
                 income["period"] != balance["period"])):
            raise FinancialDataError("Financial statement currency or period mismatch")

        def metric(item: dict, key: str) -> Decimal | None:
            value = item.get(key)
            if value is None:
                return None
            try:
                if isinstance(value, bool):
                    raise InvalidOperation
                decimal = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                raise FinancialDataError("Invalid financial metric " + key)
            if not decimal.is_finite():
                raise FinancialDataError("Nonfinite financial metric " + key)
            return decimal

        net_income = metric(income, "net_income")
        revenue = metric(income, "revenue")
        equity = metric(balance, "shareholders_equity")
        if equity is None:
            equity = metric(balance, "total_equity")
        output = {}
        if net_income is not None and revenue is not None and revenue > 0:
            output["net_profit_margin"] = str(net_income / revenue)
        if net_income is not None and equity is not None and equity > 0:
            output["return_on_period_end_equity"] = str(net_income / equity)
        if not output:
            raise FinancialDataError("No valid inputs for financial ratio calculations")
        return {
            "ticker": research["ticker"], "provider": research["provider"],
            "source_url": research["source_url"], "retrieved_at": research["retrieved_at"],
            "report_period": period_end, "currency": income.get("currency"),
            "calculation": "Net income / revenue; net income / period-end equity, where available",
            "data": {**output, "report_period": period_end},
            "input_records": [income, balance], "derived": True,
            "calculation_methods": {"net_profit_margin": "net_income / revenue",
                                    "return_on_period_end_equity": "net_income / period-end equity"},
        }

    async def compare(self, tickers: list[str]):
        if not isinstance(tickers, list) or not 2 <= len(tickers) <= 5:
            raise ValueError("Compare between 2 and 5 symbols")
        normalized = [validate_ticker(t) for t in tickers]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Comparison tickers must be distinct")
        results = await asyncio.gather(*(
            self.retrieve("get_company_fundamentals", ticker)
            for ticker in normalized
        ))
        return {
            "comparison": results,
            "note": "Provider-reported metric snapshots. Align currencies and reporting dates before ranking.",
        }

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
        return {**research, "derived": True, "input_records": [{k: v for k, v in row.items() if k in {"ticker", "time", "date", "currency", "close"}} for row in rows],
                "currency": rows[-1].get("currency"),
                "calculation_methods": {"sma": f"mean of last {window} closes", "rsi": f"simple-window RSI({window})"},
                "data": {"sma": str(sum(closes[-window:])/window),
                                     "rsi": str(rsi), "window": window},
                "calculation": "SMA and simple-window RSI from provider close prices"}

    async def risk_metrics(self, ticker: str, start_date: str, end_date: str):
        research = await self.retrieve("get_prices", ticker, start_date=start_date,
                                       end_date=end_date, interval="day", interval_multiplier=1)
        rows = sorted(research["data"], key=lambda r: str(r.get("time") or r.get("date")))
        first, last = [observation_date(row.get("time") or row.get("date")) for row in (rows[0], rows[-1])]
        values = analyze_prices([row["close"] for row in rows], elapsed_days=(last-first).days)
        return {**research, "data": values, "derived": True, "input_records": [{k: v for k, v in row.items() if k in {"ticker", "time", "date", "currency", "close"}} for row in rows],
                "currency": rows[-1].get("currency"),
                "calculation_methods": {key: "Daily closes; 252 trading days/year; sample volatility; zero annual risk-free rate; CAGR actual days/365.25; adjustment unspecified" for key in values},
                "data_timestamp": str(rows[-1].get("time") or rows[-1].get("date"))}


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
    async def get_company_profile(ticker: str):
        return await provider.retrieve("get_company_profile", ticker)
    async def calculate_financial_ratios(ticker: str, period: str = "annual"):
        return await provider.financial_ratios(ticker, period)
    async def compare_companies(tickers: list[str]):
        return await provider.compare(tickers)
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

    async def calculate_risk_metrics(ticker: str, start_date: str, end_date: str):
        return await provider.risk_metrics(ticker, start_date, end_date)

    executor.tool_signatures = {}
    for func in (get_price_snapshot, get_prices, get_company_fundamentals,
                 get_company_profile, calculate_financial_ratios, compare_companies,
                 get_financial_metrics, get_income_statements, get_balance_sheets,
                 get_cash_flow_statements, get_all_financial_statements,
                 get_news, get_filings, calculate_technical_indicators, calculate_risk_metrics):
        executor.register(func.__name__, func)
        executor.tool_signatures[func.__name__] = str(signature(func))
    return provider
