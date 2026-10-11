"""FastAPI endpoint and SSE regressions with injected deterministic dependencies."""
import json

import pytest
from fastapi.testclient import TestClient

from app import main
from dexter_py.agent.orchestrator import Orchestrator
from dexter_py.agent.schemas import Plan, PlanTask, ToolCall, Understanding, ReflectionOutput
from dexter_py.agent.tool_executor import ToolExecutor


class FakeLLM:
    def __init__(self):
        self.prompts = []

    async def complete_structured(self, *, prompt, output_model, system_prompt, **kwargs):
        self.prompts.append((output_model.__name__, prompt))
        if output_model is Understanding:
            return Understanding(intent="Retrieve mock quote")
        if output_model is Plan:
            return Plan(summary="Mock quote", tasks=[
                PlanTask(id="quote", description="Read quote", tool_calls=[
                    ToolCall(tool="fixture_quote", parameters={"ticker": "TEST"})
                ])
            ])
        if output_model is ReflectionOutput:
            return ReflectionOutput(
                is_complete=True, confidence=0.99, reasoning="Tool result exists"
            )
        raise AssertionError("Unexpected structured phase")

    async def stream_text(self, *, prompt, system_prompt):
        self.prompts.append(("answer", prompt))
        assert "'price': 125.0" in prompt
        yield "Observed mock quote"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("BACKEND_API_KEY", "test-key")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    fake = FakeLLM()
    # Prevent cross-test IP throttling; middleware remains installed in production.
    originally_enabled = main.limiter.enabled
    main.limiter.enabled = False
    try:
        context = TestClient(main.app)
        with context as client:
            tools = ToolExecutor()
            tools.register("fixture_quote", lambda ticker: {"ticker": ticker, "price": 125.0})
            main.app.state.orchestrator = Orchestrator(
                model="gpt-4o-mini", max_iterations=1,
                llm_client=fake, tool_executor=tools,
            )
            yield client, fake
    finally:
        main.limiter.enabled = originally_enabled


def _events(response):
    return [
        json.loads(part.removeprefix("data: "))
        for part in response.text.split("\n\n")
        if part.startswith("data: ")
    ]


def test_health_metrics_dashboard_and_readiness(client):
    http, _ = client
    assert http.get("/health").json() == {"status": "ok"}
    assert http.get("/ready").status_code in (200, 503)
    assert b"dexter_requests_total" in http.get("/metrics").content
    assert http.get("/dashboard").status_code == 200
    assert http.get("/api/recent").json().get("recent") is not None


def test_authentication_and_validation(client):
    http, _ = client
    assert http.post("/query", json={"prompt": "Test"}).status_code == 401
    assert http.post("/agent/query", json={"query": "Test"}).status_code == 401
    assert http.get("/agent/history").status_code == 401
    assert http.post("/agent/query", headers={"x-api-key": "test-key"},
                     json={}).status_code == 422


def test_direct_query_sse_contract(client, monkeypatch):
    http, _ = client

    async def fake_stream(prompt):
        yield "hello "
        yield "world"

    monkeypatch.setattr(main, "call_llm_stream", fake_stream)
    response = http.post(
        "/query", headers={"x-api-key": "test-key"}, json={"prompt": "hello"}
    )
    assert response.status_code == 200
    assert response.headers["x-request-id"]
    tokens = _events(response)
    assert [item["token"] for item in tokens] == ["hello ", "world"]
    assert all(item["request_id"] for item in tokens)


def test_agent_sse_persists_and_reuses_session(client):
    http, fake = client
    auth = {"x-api-key": "test-key"}
    response = http.post("/agent/query", headers=auth, json={"query": "TEST price?"})
    assert response.status_code == 200
    payload = _events(response)[0]
    assert payload["type"] == "answer"
    assert payload["content"] == "Observed mock quote"
    session_id = payload["session_id"]
    assert session_id == response.cookies["session_id"]

    history = http.get("/agent/history", headers=auth, params={"session_id": session_id})
    assert history.status_code == 200
    assert history.json()["turns"] == 1

    second = http.post("/agent/query", headers=auth, json={
        "query": "What was the earlier TEST price?", "session_id": session_id,
    })
    assert _events(second)[0]["type"] == "answer"
    assert "TEST price?" in [
        prompt for name, prompt in fake.prompts if name == "Understanding"
    ][-1]
    assert http.get("/agent/history", headers=auth,
                    params={"session_id": session_id}).json()["turns"] == 2

    cleared = http.delete("/agent/history", headers=auth,
                          params={"session_id": session_id})
    assert cleared.json()["status"] == "cleared"
    assert http.get("/agent/history", headers=auth,
                    params={"session_id": session_id}).json()["turns"] == 0


def test_agent_stream_error_does_not_commit_history(client):
    http, _ = client
    auth = {"x-api-key": "test-key"}
    session = http.post("/agent/session", headers=auth).json()["session_id"]

    class BrokenAgent:
        async def run(self, **kwargs):
            raise RuntimeError("mock execution failed")

    original = main.app.state.orchestrator
    main.app.state.orchestrator = BrokenAgent()
    try:
        res = http.post("/agent/query", headers=auth, json={
            "query": "Test", "session_id": session
        })
        assert _events(res)[0]["type"] == "error"
        assert "mock execution failed" in _events(res)[0]["error"]
        history = http.get("/agent/history", headers=auth, params={"session_id": session})
        assert history.json()["turns"] == 0
    finally:
        main.app.state.orchestrator = original
