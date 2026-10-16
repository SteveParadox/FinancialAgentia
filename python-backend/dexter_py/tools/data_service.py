"""Capability-aware aggregation on existing providers, no alternate orchestrator."""

from __future__ import annotations

import asyncio
from inspect import signature

from .financial_datasets import FinancialDataError, validate_ticker


class FinancialDataService:
    def __init__(self, market=None, filings=None):
        self.market = market
        self.filings = filings

    def capabilities(self):
        return {
            "financialdatasets.ai": ["quote", "historical_prices", "statements", "news"]
            if self.market
            else [],
            "sec.gov": ["filing_fundamentals"] if self.filings else [],
        }

    async def fundamentals(self, ticker: str):
        ticker = validate_ticker(ticker)
        requests = []
        if self.market:
            requests.append(
                self.market.retrieve(
                    "get_all_financial_statements", ticker, period="annual", limit=4
                )
            )
        if self.filings and ticker.upper() in self.filings.companies:
            requests.append(self.filings.facts(ticker))
        results = await asyncio.gather(*requests, return_exceptions=True)
        records = [result for result in results if isinstance(result, dict)]
        if not records:
            raise FinancialDataError(
                "No configured source returned usable fundamentals"
            )
        return {
            "comparison": records,
            "completeness": "partial"
            if len(records) < len(results)
            or any(r.get("completeness") == "partial" for r in records)
            else "complete",
            "coverage": "multi-source" if len(records) > 1 else "single-source",
            "limitations": [
                "Sources are kept distinct. Only matching currency and exact reporting duration can be compared.",
                *(
                    [
                        "One configured source failed; no cross-source verification claimed."
                    ]
                    if len(records) < len(results)
                    else []
                ),
            ],
        }

    def register(self, executor):
        if not self.market and not self.filings:
            return

        async def get_fundamental_evidence(ticker: str):
            """Retrieve independent configured statement sources; preserve disagreements and duration contexts."""
            return await self.fundamentals(ticker)

        executor.register("get_fundamental_evidence", get_fundamental_evidence)
        if not hasattr(executor, "tool_signatures"):
            executor.tool_signatures = {}
        executor.tool_signatures["get_fundamental_evidence"] = str(
            signature(get_fundamental_evidence)
        )
