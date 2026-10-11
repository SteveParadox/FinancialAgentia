"""Offline financial research provider integration tests."""
import asyncio
import httpx
import pytest
from dexter_py.agent.tool_executor import ToolExecutor
from dexter_py.tools.financial_datasets import FinancialDatasetsClient, FinancialDataError, register_financial_tools

def run(coroutine):
    return asyncio.run(coroutine)

def test_quote_origin_and_source():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"snapshot": {"price": 185.50, "time": "2026-10-09"}})
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            provider = FinancialDatasetsClient("test-secret", http_client=http)
            result = await provider.retrieve("get_price_snapshot", "aapl")
            assert result["ticker"] == "AAPL"
            assert result["data"]["price"] == 185.50
            assert "test-secret" not in result["source_url"]
            assert requests[0].url.path == "/prices/snapshot/"
            assert requests[0].url.params["ticker"] == "AAPL"
    run(check())

def test_no_fabricated_values():
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"snapshot": None})
        )) as http:
            provider = FinancialDatasetsClient("test-secret", http_client=http)
            with pytest.raises(FinancialDataError, match="no usable"):
                await provider.retrieve("get_price_snapshot", "AAPL")
    run(check())

def test_bad_request_rejected():
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"prices": []})
        )) as http:
            provider = FinancialDatasetsClient("test-secret", http_client=http)
            with pytest.raises(ValueError):
                await provider.retrieve("get_price_snapshot", "../../secrets")
            with pytest.raises(ValueError):
                await provider.retrieve("get_prices", "AAPL", start_date="2099-01-01", end_date="2099-01-02")
            with pytest.raises(ValueError):
                await provider.retrieve("get_news", "AAPL", limit=101)
    run(check())

def test_registry_fail_closed(monkeypatch):
    monkeypatch.delenv("FINANCIAL_DATASETS_API_KEY", raising=False)
    executor = ToolExecutor()
    assert register_financial_tools(executor) is None
    assert executor.tools == {}

def test_registered_tool_signatures():
    executor = ToolExecutor()
    provider = register_financial_tools(executor, api_key="fake-test-key")
    assert provider is not None
    assert "get_price_snapshot" in executor.tools
    assert "get_all_financial_statements" in executor.tools
    assert "get_news" in executor.tools
    assert "ticker" in executor.tool_signatures["get_news"]
    run(provider.close())
