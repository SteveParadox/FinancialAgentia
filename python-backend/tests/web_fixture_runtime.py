"""Browser CI runtime. Only upstream HTTP/model responses are deterministic fixtures.

This file is never loaded by production. API, Redis, worker, orchestrator,
financial tools, official OpenAI SDK, validation and browser code are genuine.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import date, timedelta, datetime, timezone
import json
import math
import os
import uuid
from inspect import signature

import httpx
import uvicorn
from app import main
from app.execution_worker import ExecutionWorker
from dexter_py.agent.orchestrator import Orchestrator
from dexter_py.agent.schemas import Understanding, Plan, PlanTask, ToolCall, ReflectionOutput
from dexter_py.agent.financial_evidence import ProposedClaims
from dexter_py.agent.tool_executor import ToolExecutor
from dexter_py.model import llm
from dexter_py.model.openai_client import OpenAIChatClient
from dexter_py.tools.financial_datasets import FinancialDatasetsClient
from test_openai_transport import completion

original_lifespan = main.app.router.lifespan_context


def scope_from(text):
    marker = 'Validated research scope (use these instruments and dates): '
    if marker not in text:
        return {'symbols': ['MSFT'], 'category': 'company'}
    return json.JSONDecoder().raw_decode(text.split(marker, 1)[1])[0]


async def model_reply(request):
    body = json.loads(request.content)
    text = body['messages'][-1]['content']
    if body.get('stream'):
        frames = [
            {'id': 'chatcmpl-fixture', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'gpt-4.1', 'choices': [{'index': 0, 'delta': {'content': 'Untrusted fixture draft; evidence validation is authoritative.'}, 'finish_reason': None}]},
            {'id': 'chatcmpl-fixture', 'object': 'chat.completion.chunk', 'created': 1,
             'model': 'gpt-4.1', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
        ]
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                              text=''.join('data: '+json.dumps(frame)+'\n\n' for frame in frames)+'data: [DONE]\n\n')
    schema = json.loads(text.rsplit('```json\n', 1)[-1].split('\n```')[0])
    title = schema['title']
    scope = scope_from(text)
    symbols = scope.get('symbols') or ['MSFT']
    if title == 'Understanding':
        result = Understanding(intent='Financial research with fixture data at the external boundary')
    elif title == 'Plan':
        tasks = []
        for symbol in symbols:
            if scope.get('category') == 'historical_prices':
                calls = [('calculate_risk_metrics', {'ticker': symbol, 'start_date': scope['start_date'], 'end_date': scope['end_date']})]
            else:
                calls = [('get_all_financial_statements', {'ticker': symbol}),
                         ('calculate_financial_ratios', {'ticker': symbol}),
                         ('get_price_snapshot', {'ticker': symbol})]
            for i, (tool, parameters) in enumerate(calls):
                tasks.append(PlanTask(id=f'{symbol}_{i}', description=tool+' for '+symbol,
                                      tool_calls=[ToolCall(tool=tool, parameters=parameters)], max_retries=0))
        result = Plan(summary='Source-backed company or historical price analysis', tasks=tasks)
    elif title == 'ReflectionOutput':
        result = ReflectionOutput(is_complete=True, confidence=.9, reasoning='Fixture coverage; validate actual evidence')
    elif title == 'ProposedClaims':
        result = ProposedClaims()
    else:
        raise AssertionError(title)
    return httpx.Response(200, json=completion(result.model_dump_json()))


async def financial_reply(request):
    ticker = request.url.params.get('ticker', 'MSFT')
    if ticker == 'SLOW':
        await asyncio.sleep(8)
    if ticker == 'FAIL':
        return httpx.Response(503, headers={'retry-after': '0'})
    common = {'ticker': ticker, 'currency': 'USD', 'report_period': '2025-12-31', 'period': 'annual'}
    if request.url.path == '/financials':
        scale = {'MSFT': 1, 'AAPL': 2, 'GOOGL': 3}.get(ticker, 1)
        return httpx.Response(200, json={'financials': {
            'income_statements': [{**common, 'revenue': 1000*scale, 'net_income': 200*scale,
                                   'gross_profit': 600*scale, 'operating_income': 300*scale, 'interest_expense': 10*scale}],
            'balance_sheets': [{**common, 'total_assets': 2000*scale, 'shareholders_equity': 800*scale,
                                'current_assets': 500*scale, 'current_liabilities': 250*scale, 'total_debt': 400*scale}],
            'cash_flow_statements': [{**common, 'operating_cash_flow': 250*scale, 'free_cash_flow': 150*scale}],
        }})
    if request.url.path == '/prices':
        start = date.fromisoformat(request.url.params['start_date'])
        end = date.fromisoformat(request.url.params['end_date'])
        days = [start+timedelta(days=i) for i in range((end-start).days+1)]
        rows = [{'ticker': ticker, 'currency': 'USD', 'time': day.isoformat()+'T20:00:00Z',
                 'close': round(100 + index*.025 + 3*math.sin(index/17), 6)}
                for index, day in enumerate(day for day in days if day.weekday() < 5)]
        return httpx.Response(200, json={'prices': rows})
    return httpx.Response(200, json={'snapshot': {'ticker': ticker, 'price': 300, 'currency': 'USD', 'time': datetime.now(timezone.utc).isoformat()}})


@asynccontextmanager
async def fixture_lifespan(app):
    async with original_lifespan(app):
        provider_http = httpx.AsyncClient(transport=httpx.MockTransport(financial_reply))
        provider = FinancialDatasetsClient('external-fixture', http_client=provider_http)
        model_client = OpenAIChatClient('gpt-4.1', 'external-fixture', http_client=httpx.AsyncClient(transport=httpx.MockTransport(model_reply)))
        original_client = llm.get_llm_client
        async def get_client(model):
            return model_client
        llm.get_llm_client = get_client
        tools = ToolExecutor()
        async def get_price_snapshot(ticker: str):
            return await provider.retrieve('get_price_snapshot', ticker)
        async def get_all_financial_statements(ticker: str, period: str = 'annual', limit: int = 4):
            return await provider.retrieve('get_all_financial_statements', ticker, period=period, limit=limit)
        tools.tool_signatures = {}
        for name, function in [('get_price_snapshot', get_price_snapshot),
                               ('get_all_financial_statements', get_all_financial_statements),
                               ('calculate_financial_ratios', provider.financial_ratios),
                               ('calculate_risk_metrics', provider.risk_metrics)]:
            tools.register(name, function)
            tools.tool_signatures[name] = str(signature(function))
        agent = Orchestrator('openai/gpt-4.1', max_iterations=1, tool_executor=tools)
        app.state.orchestrator = agent
        app.state.financial_provider = provider
        worker = ExecutionWorker(app.state.execution_registry, agent, lease_seconds=5)
        processing = asyncio.create_task(worker.run())
        try:
            yield
        finally:
            worker.stopping.set()
            await processing
            llm.get_llm_client = original_client
            await provider_http.aclose()
            await model_client.aclose()
            registry = app.state.execution_registry
            keys = [key async for key in registry.redis.scan_iter(match=registry.prefix+'*')]
            if keys:
                await registry.redis.delete(*keys)


if __name__ == '__main__':
    if not os.getenv('TEST_REDIS_URL'):
        raise SystemExit('TEST_REDIS_URL required; use a disposable Redis database')
    os.environ.update(REDIS_URL=os.environ['TEST_REDIS_URL'], BACKEND_ENV='development',
                      EXECUTION_REDIS_PREFIX='browser-'+uuid.uuid4().hex+':', JWT_SECRET='browser-fixture-secret',
                      OPENAI_API_KEY='fixture-not-live', LLM_MODEL='openai/gpt-4.1')
    os.environ.pop('FINANCIAL_DATASETS_API_KEY', None)
    main.app.router.lifespan_context = fixture_lifespan
    uvicorn.run(main.app, host='127.0.0.1', port=int(os.getenv('WEB_TEST_PORT', '8765')), log_level='warning')
