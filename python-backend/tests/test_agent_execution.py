"""Deterministic Phase 1 execution tests. No network or paid LLM required."""
import asyncio
import importlib

import pytest

from dexter_py.agent.schemas import Plan, PlanTask, ToolCall, Understanding, ReflectionOutput
from dexter_py.agent.orchestrator import Orchestrator, ExecutionContext
from dexter_py.agent.tool_executor import ToolExecutor
from dexter_py.exceptions import OrchestratorError
from dexter_py.utils.message_history import MessageHistory


class FakeLLM:
    def __init__(self, *, tasks=True, invalid=False):
        self.calls = []
        self.tasks = tasks
        self.invalid = invalid

    async def complete_structured(self, *, prompt, output_model, system_prompt, **kwargs):
        self.calls.append((output_model.__name__, prompt, system_prompt))
        if output_model is Understanding:
            return Understanding(intent="Research example ticker")
        if output_model is Plan:
            if self.invalid:
                return Plan(summary="invalid", tasks=[
                    PlanTask(id="x", description="bad dependency", depends_on=["missing"])
                ])
            return Plan(summary="Get a test quote", tasks=(
                [PlanTask(id="quote", description="Fetch demo quote", tool_calls=[
                    ToolCall(tool="mock_quote", parameters={"ticker": "TEST"})
                ])] if self.tasks else []
            ))
        if output_model is ReflectionOutput:
            return ReflectionOutput(
                is_complete=True, confidence=0.95,
                reasoning="Validated tool result" if self.tasks else "No data",
            )
        raise AssertionError("Unknown phase")

    async def stream_text(self, *, prompt, system_prompt):
        self.calls.append(("Answer", prompt, system_prompt))
        yield "Answer: "
        yield "based on retrieved evidence." if self.tasks else "No external data retrieved."


def make_tools():
    tools = ToolExecutor()
    tools.register("mock_quote", lambda ticker: {"ticker": ticker, "price": 125.0})
    return tools


def test_required_agent_modules_import():
    for module in (
        "dexter_py.agent.orchestrator", "dexter_py.agent.schemas",
        "dexter_py.agent.state", "dexter_py.agent.task_executor",
        "dexter_py.agent.tool_executor", "dexter_py.agent.phases.execute",
        "dexter_py.agent.phases.understand", "dexter_py.agent.phases.plan",
        "dexter_py.agent.phases.reflect", "dexter_py.agent.phases.answer",
        "dexter_py.model.llm", "dexter_py.model.llm_integration",
        "dexter_py.utils.session_store", "dexter_py.utils.message_history",
    ):
        importlib.import_module(module)


@pytest.mark.parametrize("kwargs", [
    {"model": ""}, {"model": "gpt-4o-mini", "max_iterations": 0},
    {"model": "gpt-4o-mini", "max_iterations": 11},
    {"model": "gpt-4o-mini", "max_iterations": True},
])
def test_reject_invalid_options(kwargs):
    with pytest.raises(ValueError):
        Orchestrator(**kwargs)


@pytest.mark.parametrize("dependencies", [
    (["missing"], ["x"]),
    (["x"], ["x"]),
])
def test_invalid_dependencies(dependencies):
    depends_on, ids = dependencies
    with pytest.raises(ValueError):
        Plan(summary="bad", tasks=[
            PlanTask(id=ids[0], description="bad", depends_on=depends_on),
        ]).get_execution_order()


def test_duplicate_task_ids():
    plan = Plan(summary="duplicate", tasks=[
        PlanTask(id="x", description="one"),
        PlanTask(id="x", description="two"),
    ])
    with pytest.raises(ValueError, match="Duplicate"):
        plan.get_execution_order()


def test_cyclic_plan():
    plan = Plan(summary="cycle", tasks=[
        PlanTask(id="a", description="a", depends_on=["b"]),
        PlanTask(id="b", description="b", depends_on=["a"]),
    ])
    with pytest.raises(ValueError, match="Cyclic"):
        plan.get_execution_order()


def test_full_lifecycle_and_history_context():
    async def scenario():
        fake = FakeLLM()
        agent = Orchestrator("gpt-4o-mini", max_iterations=1,
                             llm_client=fake, tool_executor=make_tools())
        history = MessageHistory(model="gpt-4o-mini")
        await history.add_message("Earlier question", "Earlier answer")
        answer = await agent.run("What is the TEST quote?", message_history=history)
        assert "retrieved evidence" in answer
        names = [name for name, *_ in fake.calls]
        assert names == ["Understanding", "Plan", "ReflectionOutput", "Answer"]
        assert "Earlier question" in fake.calls[0][1]
        assert "125.0" in fake.calls[-1][1]
        assert "financial research planner" in fake.calls[1][2]
        # Orchestrator does not persist: the API owns the commit.
        assert len(history) == 1

    asyncio.run(scenario())


def test_missing_tools_not_fake_success():
    async def scenario():
        agent = Orchestrator("gpt-4o-mini", max_iterations=1,
                             llm_client=FakeLLM(), tool_executor=ToolExecutor())
        with pytest.raises(ValueError, match="Unknown tools"):
            await agent.run("Price check")

    asyncio.run(scenario())


def test_malformed_plan_does_not_execute_tools():
    async def scenario():
        called = []
        tools = ToolExecutor()
        tools.register("mock_quote", lambda ticker: called.append(ticker))
        agent = Orchestrator("gpt-4o-mini", max_iterations=1,
                             llm_client=FakeLLM(invalid=True), tool_executor=tools)
        with pytest.raises(ValueError, match="Unknown dependency"):
            await agent.run("bad plan")
        assert called == []

    asyncio.run(scenario())


def test_retries_bounded_and_failures_recorded():
    class BrokenExecutor:
        def __init__(self):
            self.count = 0

        async def execute(self, name, params):
            self.count += 1
            raise RuntimeError("provider failure")

    async def scenario():
        tool = BrokenExecutor()
        agent = Orchestrator("gpt-4o-mini", max_iterations=1,
                             llm_client=FakeLLM(), tool_executor=tool)
        ctx = ExecutionContext(
            run_id="test", query="TEST", model="gpt-4o-mini",
            current_plan=Plan(summary="retry", tasks=[
                PlanTask(id="t1", description="retry", max_retries=2,
                         tool_calls=[ToolCall(tool="broken")]),
                PlanTask(id="t2", description="dependent", depends_on=["t1"],
                         tool_calls=[ToolCall(tool="broken")]),
            ]),
        )
        await agent._execute(ctx)
        assert tool.count == 3
        assert ctx.current_plan.tasks[0].attempt_count == 3
        assert ctx.current_plan.tasks[0].status == "failed"
        assert ctx.current_plan.tasks[1].status == "skipped"
        assert ctx.task_results["t1"]["status"] == "failed"
        assert ctx.task_results["t2"]["status"] == "skipped"

    asyncio.run(scenario())


def test_llm_failure_propagates():
    class TimeoutLLM(FakeLLM):
        async def complete_structured(self, **kwargs):
            raise TimeoutError("mock timeout")

    async def scenario():
        agent = Orchestrator("gpt-4o-mini", max_iterations=1,
                             llm_client=TimeoutLLM(), tool_executor=make_tools())
        with pytest.raises(TimeoutError, match="mock timeout"):
            await agent.run("Hello")

    asyncio.run(scenario())


def test_blank_answer_fails():
    class BlankAnswer(FakeLLM):
        async def stream_text(self, **kwargs):
            yield ""

    async def scenario():
        agent = Orchestrator("gpt-4o-mini", max_iterations=1,
                             llm_client=BlankAnswer(tasks=False),
                             tool_executor=ToolExecutor())
        with pytest.raises(OrchestratorError, match="empty response"):
            await agent.run("Question")

    asyncio.run(scenario())

def test_unknown_tool_rejected_before_prior_valid_task_runs():
    async def scenario():
        called = []
        tools = ToolExecutor()
        tools.register("valid", lambda: called.append("ran"))
        context = ExecutionContext(
            run_id="unknown", query="test", model="gpt-4o-mini",
            current_plan=Plan(summary="unknown tool", tasks=[
                PlanTask(id="valid", description="good",
                         tool_calls=[ToolCall(tool="valid")]),
                PlanTask(id="invalid", description="invalid",
                         depends_on=["valid"],
                         tool_calls=[ToolCall(tool="nonexistent")]),
            ]),
        )
        agent = Orchestrator("gpt-4o-mini", llm_client=FakeLLM(),
                             tool_executor=tools)
        with pytest.raises(ValueError, match="Unknown tools"):
            await agent._execute(context)
        assert called == []

    asyncio.run(scenario())


def test_successful_tool_call_is_not_repeated_after_later_tool_failure():
    class Flaky:
        def __init__(self):
            self.called = {"first": 0, "second": 0}

        async def execute(self, name, params):
            self.called[name] += 1
            if name == "second" and self.called[name] == 1:
                raise RuntimeError("temporary")
            return name

    async def scenario():
        tool = Flaky()
        context = ExecutionContext(
            run_id="retry", query="test", model="gpt-4o-mini",
            current_plan=Plan(summary="two tools", tasks=[
                PlanTask(id="both", description="needs tools",
                         max_retries=1,
                         tool_calls=[
                             ToolCall(tool="first"), ToolCall(tool="second"),
                         ]),
            ]),
        )
        agent = Orchestrator("gpt-4o-mini", llm_client=FakeLLM(),
                             tool_executor=tool)
        await agent._execute(context)
        assert tool.called == {"first": 1, "second": 2}
        assert context.current_plan.tasks[0].status == "completed"

    asyncio.run(scenario())
