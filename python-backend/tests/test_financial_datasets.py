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


def test_financial_ratios_preserve_source_and_period():
    statements = {
        "financials": {
            "income_statements": [
                {"report_period": "2025-12-31", "currency": "USD", "revenue": 100, "net_income": 20}
            ],
            "balance_sheets": [
                {"report_period": "2025-12-31", "currency": "USD", "shareholders_equity": 40}
            ],
        }
    }
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json=statements)
        )) as http:
            provider = FinancialDatasetsClient("test-key", http_client=http)
            answer = await provider.financial_ratios("AAPL")
            assert answer["data"]["net_profit_margin"] == "0.2"
            assert answer["data"]["return_on_period_end_equity"] == "0.5"
            assert answer["report_period"] == "2025-12-31"
            assert "financials" in answer["source_url"]
    run(check())

def test_financial_ratios_reject_mismatched_reporting_periods():
    statements = {"financials": {
        "income_statements": [{"report_period": "2025-12-31", "currency": "USD", "revenue": 100, "net_income": 20}],
        "balance_sheets": [{"report_period": "2024-12-31", "currency": "USD", "shareholders_equity": 40}],
    }}
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json=statements)
        )) as http:
            provider = FinancialDatasetsClient("test-key", http_client=http)
            with pytest.raises(FinancialDataError, match="mismatch"):
                await provider.financial_ratios("AAPL")
    run(check())

def test_invalid_quote_fails_closed():
    async def check():
        for invalid in ({"ticker": "MSFT", "price": 200},
                        {"ticker": "AAPL", "price": -4},
                        {"ticker": "AAPL", "price": "NaN"},
                        {"ticker": "AAPL", "time": "2026-10-11"}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json={"snapshot": invalid})
            )) as http:
                provider = FinancialDatasetsClient("test-secret", http_client=http)
                with pytest.raises(FinancialDataError):
                    await provider.retrieve("get_price_snapshot", "AAPL")
    run(check())

def test_malformed_financial_data_is_not_marked_success():
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"financials": {"unexpected": True}})
        )) as http:
            provider = FinancialDatasetsClient("test-secret", http_client=http)
            with pytest.raises(FinancialDataError, match="combined"):
                await provider.retrieve("get_all_financial_statements", "AAPL", period="annual", limit=1)
    run(check())

def test_invalid_historical_closes_fail_closed():
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"prices": [{"ticker": "AAPL", "close": -1}]})
        )) as http:
            provider = FinancialDatasetsClient("test-secret", http_client=http)
            with pytest.raises(FinancialDataError, match="Historical price"):
                await provider.retrieve("get_prices", "AAPL", start_date="2026-09-01", end_date="2026-09-10")
    run(check())

def test_partial_statement_results_are_explicit():
    data = {"financials": {"income_statements": [
        {"ticker": "AAPL", "report_period": "2025-12-31", "revenue": 100}
    ], "balance_sheets": [], "cash_flow_statements": []}}
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json=data)
        )) as http:
            provider = FinancialDatasetsClient("test-secret", http_client=http)
            result = await provider.retrieve("get_all_financial_statements",
                                             "AAPL", period="annual", limit=1)
            assert result["completeness"] == "partial"
            assert result["data_timestamp"] == "2025-12-31"
            assert set(result["missing_statements"]) == {
                "balance_sheets", "cash_flow_statements"
            }
    run(check())

def test_invalid_tool_parameters_are_rejected_before_execution():
    count = {"calls": 0}
    def dummy(ticker: str):
        count["calls"] += 1
        return ticker
    executor = ToolExecutor()
    executor.register("dummy", dummy)
    with pytest.raises(ValueError, match="Invalid arguments"):
        run(executor.execute("dummy", {"symbol": "AAPL"}))
    assert count["calls"] == 0

def test_async_circuit_breaker_stops_repeated_provider_failures():
    from dexter_py.agent.tool_executor import ToolCircuitOpen
    executor = ToolExecutor()
    invoked = {"count": 0}
    async def broken(ticker: str):
        invoked["count"] += 1
        raise RuntimeError("Provider connection refused")
    executor.register("broken", broken, fail_max=2)
    async def check():
        for _ in range(2):
            with pytest.raises(RuntimeError, match="Provider connection refused"):
                await executor.execute("broken", {"ticker": "AAPL"})
        with pytest.raises(ToolCircuitOpen):
            await executor.execute("broken", {"ticker": "AAPL"})
    run(check())
    assert invoked["count"] == 2
