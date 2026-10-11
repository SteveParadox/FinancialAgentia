"""Agent registration and the versioned FastAPI cross-runtime contract."""
from fastapi.testclient import TestClient
from app import main

def test_runtime_catalog_requires_auth_and_registers_actual_financial_tools(monkeypatch):
    monkeypatch.setenv("BACKEND_API_KEY", "fixture-api-key")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("FINANCIAL_DATASETS_API_KEY", "fixture-provider-key")
    monkeypatch.delenv("REDIS_URL", raising=False)
    with TestClient(main.app) as client:
        assert client.get("/api/v1/tools").status_code == 401
        found = client.get("/api/v1/tools", headers={"X-API-Key": "fixture-api-key"})
        assert found.status_code == 200
        names = {tool["name"] for tool in found.json()["tools"]}
        assert {"get_price_snapshot", "get_news", "get_all_financial_statements"} <= names
        assert client.get("/ready").json()["financial_tools_registered"] is True
        assert any(route.path == "/api/v1/agents/execute" for route in main.app.routes)

def test_provider_registration_absent_when_unconfigured(monkeypatch):
    monkeypatch.delenv("FINANCIAL_DATASETS_API_KEY", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    with TestClient(main.app) as client:
        found = client.get("/api/v1/tools")
        assert found.status_code == 200
        assert found.json()["tools"] == []
        assert client.get("/ready").json()["financial_tools_registered"] is False
