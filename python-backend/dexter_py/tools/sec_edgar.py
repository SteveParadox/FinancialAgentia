"""SEC XBRL company facts: explicit issuer mapping, original units and filing dates.

SEC contact User-Agent is mandatory. This is a filing source, never a quote feed.
No ticker search/CIK guessing or arbitrary network destinations are permitted.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import date, datetime, timezone
from decimal import Decimal
from inspect import signature
import json
import os
import re
import time

import httpx

from .financial_datasets import (
    FinancialDataError,
    FinancialDataTransportError,
    validate_ticker,
)

TAGS = {
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
    "Revenues": "revenue",
    "NetIncomeLoss": "net_income",
    "GrossProfit": "gross_profit",
    "OperatingIncomeLoss": "operating_income",
    "Assets": "total_assets",
    "Liabilities": "total_liabilities",
    "StockholdersEquity": "shareholders_equity",
    "AssetsCurrent": "current_assets",
    "LiabilitiesCurrent": "current_liabilities",
    "NetCashProvidedByUsedInOperatingActivities": "operating_cash_flow",
    "CashAndCashEquivalentsAtCarryingValue": "cash_and_equivalents",
}


class SecEdgarClient:
    def __init__(self, user_agent: str, companies: dict, *, client=None):
        if not re.search(r"\S+@\S+\.\S+", user_agent) or len(user_agent) > 250:
            raise ValueError(
                "SEC_USER_AGENT must identify organization and contact email"
            )
        if not isinstance(companies, dict) or not companies or len(companies) > 1000:
            raise ValueError(
                "SEC_COMPANIES must contain 1..1000 explicit issuer mappings"
            )
        self.companies = {}
        for symbol, item in companies.items():
            symbol = validate_ticker(symbol)
            if not isinstance(item, dict) or not re.fullmatch(
                r"[0-9]{1,10}", str(item.get("cik", ""))
            ):
                raise ValueError(
                    "SEC_COMPANIES requires explicit symbol to CIK mappings"
                )
            if int(item["cik"]) == 0 or any(
                k in item
                and (not isinstance(item[k], str) or not 1 <= len(item[k]) <= 120)
                for k in ("name", "exchange")
            ):
                raise ValueError("Invalid SEC issuer metadata")
            self.companies[symbol] = {**item, "cik": str(item["cik"]).zfill(10)}
        self.client = client or httpx.AsyncClient(
            timeout=15, follow_redirects=False, headers={"User-Agent": user_agent}
        )
        self._owned = client is None
        self._lock = asyncio.Lock()
        self._cache = {}
        self._next = 0.0
        self.redis = None

    async def close(self):
        if self._owned:
            await self.client.aclose()

    async def facts(self, ticker: str, as_of: str | None = None):
        ticker = validate_ticker(ticker)
        company = self.companies.get(ticker)
        if company is None:
            raise FinancialDataError("No authorized SEC issuer mapping for symbol")
        cutoff = (
            date.fromisoformat(as_of) if as_of else datetime.now(timezone.utc).date()
        )
        if cutoff > datetime.now(timezone.utc).date():
            raise ValueError("Future SEC research cutoff")
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{company['cik']}.json"
        # Cache separate vintages; preserve retrieval date. No stale-on-error.
        key = (ticker, cutoff.isoformat())
        async with self._lock:
            cached = self._cache.get(key)
            if cached and time.monotonic() - cached[0] < 300:
                return {**copy.deepcopy(cached[1]), "cache_status": "cached"}
            payload = None
            for attempt in range(3):
                await asyncio.sleep(max(0, self._next - time.monotonic()))
                if self.redis is not None:
                    # One request/second across all API and worker replicas;
                    # failure to acquire cannot authorize an upstream request.
                    async with asyncio.timeout(20):
                        while not await self.redis.set(
                            "financialagentia:sec:fair_access", "1", nx=True, px=1000
                        ):
                            await asyncio.sleep(0.1)
                self._next = (
                    time.monotonic() + 1
                )  # conservative per-process, deployment must cap total workers
                try:
                    async with self.client.stream("GET", url) as response:
                        if response.status_code in {429, 500, 502, 503, 504}:
                            if attempt == 2:
                                raise FinancialDataTransportError(
                                    "SEC temporarily unavailable"
                                )
                            await asyncio.sleep(min(5, 2**attempt))
                            continue
                        if response.status_code != 200:
                            raise FinancialDataError(
                                "SEC request rejected or issuer unavailable"
                            )
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > 15_000_000:
                                raise FinancialDataError(
                                    "SEC response exceeds size limit"
                                )
                        payload = json.loads(data)
                        break
                except httpx.TransportError as exc:
                    if attempt == 2:
                        raise FinancialDataTransportError(
                            "SEC connection failed"
                        ) from exc
                    await asyncio.sleep(2**attempt)
            if (
                not isinstance(payload, dict)
                or str(payload.get("cik", "")).zfill(10) != company["cik"]
            ):
                raise FinancialDataError("SEC issuer identity mismatch")
            try:
                facts = payload["facts"].get("us-gaap", {})
            except (KeyError, AttributeError, TypeError) as exc:
                raise FinancialDataError("Malformed SEC company facts schema") from exc
            if not isinstance(facts, dict):
                raise FinancialDataError("Malformed SEC taxonomy schema")
            records = []
            # Distinct duration contexts and restatements remain distinct rows.
            seen = set()
            for tag, metric in TAGS.items():
                concept = facts.get(tag, {})
                if not isinstance(concept, dict) or not isinstance(
                    concept.get("units", {}), dict
                ):
                    raise FinancialDataError("Malformed SEC concept units")
                for unit, observations in concept.get("units", {}).items():
                    if not isinstance(observations, list) or any(
                        not isinstance(row, dict) for row in observations
                    ):
                        raise FinancialDataError("Malformed SEC observations")
                    if not re.fullmatch(r"[A-Z]{3}", unit):
                        continue
                    candidates = {}
                    for row in observations:
                        try:
                            end = date.fromisoformat(row["end"])
                            filed = date.fromisoformat(row["filed"])
                            start = (
                                date.fromisoformat(row["start"])
                                if row.get("start")
                                else None
                            )
                            value = Decimal(str(row["val"]))
                        except (ValueError, KeyError, TypeError, ArithmeticError):
                            continue
                        if (
                            not value.is_finite()
                            or isinstance(row["val"], bool)
                            or end > cutoff
                            or filed > cutoff
                            or filed < end
                            or (start and start > end)
                        ):
                            continue
                        context = (start, end, unit)
                        if (
                            context not in candidates
                            or row["filed"] > candidates[context]["filed"]
                        ):
                            candidates[context] = row
                    for (start, end, currency), row in sorted(
                        candidates.items(), key=lambda pair: pair[0][1], reverse=True
                    )[:12]:
                        identity = (metric, start, end, currency)
                        if identity in seen:
                            continue  # explicit tag priority above, not arbitrary sum
                        seen.add(identity)
                        accession = row.get("accn", "")
                        filing_url = (
                            f"https://www.sec.gov/Archives/edgar/data/{int(company['cik'])}/{accession.replace('-', '')}/"
                            if re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession)
                            else None
                        )
                        records.append(
                            {
                                "ticker": ticker,
                                metric: str(row["val"]),
                                "currency": currency,
                                "report_period": end.isoformat(),
                                "period_start": start.isoformat() if start else None,
                                "filing_date": row["filed"],
                                "fiscal_period": row.get("fp"),
                                "filing_url": filing_url,
                                "source_concept": tag,
                                "accession_number": accession,
                                "units": currency,
                                "restatement_policy": "latest filing available by as_of for identical duration context",
                            }
                        )
            result = {
                "provider": "sec.gov",
                "ticker": ticker,
                "issuer_id": "SEC:CIK:" + company["cik"],
                "source_url": url,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "exchange": company.get("exchange"),
                "as_of": cutoff.isoformat(),
                "cache_status": "fresh",
                "data": records,
                "adjustment_status": "not_applicable",
                "completeness": "partial",
                "limitations": [
                    "Selected US-GAAP concepts only; no quote data; duration contexts must be aligned before ratios."
                ],
            }
            if len(self._cache) >= 128:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (time.monotonic(), copy.deepcopy(result))
            return result


def register_sec_tools(executor):
    if not os.getenv("SEC_USER_AGENT"):
        return None
    provider = SecEdgarClient(
        os.environ["SEC_USER_AGENT"], json.loads(os.getenv("SEC_COMPANIES", "{}"))
    )

    async def get_sec_company_facts(ticker: str, as_of: str | None = None):
        """Reported SEC fundamentals; as_of restricts both filing and period dates."""
        return await provider.facts(ticker, as_of)

    executor.register("get_sec_company_facts", get_sec_company_facts)
    if not hasattr(executor, "tool_signatures"):
        executor.tool_signatures = {}
    executor.tool_signatures["get_sec_company_facts"] = str(
        signature(get_sec_company_facts)
    )
    return provider
