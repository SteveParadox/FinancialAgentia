"""Offline socket-level E2E fixture, never imported by production startup."""
import asyncio
from contextlib import asynccontextmanager

import httpx
import uvicorn
from app import main
from app.execution_worker import ExecutionWorker
from dexter_py.agent.orchestrator import Orchestrator
from dexter_py.agent.tool_executor import ToolExecutor
from dexter_py.tools.financial_datasets import FinancialDatasetsClient
from test_durable_pipeline import ResearchLLM

original_lifespan = main.app.router.lifespan_context


@asynccontextmanager
async def fixture_lifespan(app):
    async with original_lifespan(app):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={
            'snapshot': {'ticker': 'AAPL', 'price': 100, 'currency': 'USD', 'time': '2026-09-03T20:00:00Z'},
            'prices': [{'ticker': 'AAPL', 'currency': 'USD', 'time': f'2026-09-0{i}T20:00:00Z', 'close': close}
                       for i, close in enumerate([100, 110, 99], 1)],
        }))) as http:
            provider = FinancialDatasetsClient('fixture', http_client=http)
            tools = ToolExecutor()
            async def quote(ticker):
                return await provider.retrieve('get_price_snapshot', ticker)
            tools.register('get_price_snapshot', quote)
            tools.register('calculate_risk_metrics', provider.risk_metrics)
            agent = Orchestrator('gpt-4o-mini', max_iterations=1, llm_client=ResearchLLM(), tool_executor=tools)
            app.state.orchestrator = agent
            worker = ExecutionWorker(app.state.execution_registry, agent)
            processing = asyncio.create_task(worker.run())
            try:
                yield
            finally:
                worker.stopping.set()
                await processing


if __name__ == '__main__':
    import sys
    main.app.router.lifespan_context = fixture_lifespan
    uvicorn.run(main.app, host='127.0.0.1', port=int(sys.argv[1]), log_level='warning')
