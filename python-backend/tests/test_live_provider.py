"""Opt-in LIVE provider smoke tests. Never claim a mock is a live call.

FINANCIAL_RESEARCH_LIVE_TESTS=true and FINANCIAL_DATASETS_API_KEY required.
Tests respect provider entitlements and do not place trades.
"""
import asyncio
import os

import pytest

from dexter_py.tools.financial_datasets import FinancialDatasetsClient

RUN_LIVE = os.getenv("FINANCIAL_RESEARCH_LIVE_TESTS", "").lower() == "true"
KEY = os.getenv("FINANCIAL_DATASETS_API_KEY", "")


@pytest.mark.skipif(not RUN_LIVE or not KEY,
                    reason="Live provider credentials/explicit opt-in missing; NOT VERIFIED")
@pytest.mark.parametrize("operation,options", [
    ("get_price_snapshot", {}),
    ("get_prices", {"start_date": "2025-10-01", "end_date": "2025-10-31", "interval": "day"}),
    ("get_company_profile", {}),
    ("get_company_fundamentals", {}),
    ("get_financial_metrics", {"period": "annual", "limit": 2}),
    ("get_income_statements", {"period": "annual", "limit": 2}),
    ("get_income_statements", {"period": "quarterly", "limit": 2}),
    ("get_balance_sheets", {"period": "annual", "limit": 2}),
    ("get_cash_flow_statements", {"period": "annual", "limit": 2}),
    ("get_news", {"limit": 2}),
    ("get_filings", {"limit": 2}),
])
def test_real_financial_datasets_operation(operation, options):
    async def check():
        client = FinancialDatasetsClient(KEY)
        try:
            result = await client.retrieve(operation, "AAPL", **options)
            assert result["provider"] == "financialdatasets.ai"
            assert result["ticker"] == "AAPL"
            assert result["source_url"].startswith("https://api.financialdatasets.ai/")
            assert result["data"]
        finally:
            await client.close()
    asyncio.run(check())


@pytest.mark.skipif(not RUN_LIVE or not KEY,
                    reason="Live provider credentials/explicit opt-in missing; NOT VERIFIED")
def test_real_company_comparison_and_indicators():
    async def check():
        client = FinancialDatasetsClient(KEY)
        try:
            result = await client.compare(["AAPL", "MSFT"])
            assert len(result["comparison"]) == 2
            indicators = await client.indicators("AAPL", "2025-08-01", "2025-10-31")
            assert "sma" in indicators["data"]
            assert "rsi" in indicators["data"]
        finally:
            await client.close()
    asyncio.run(check())
