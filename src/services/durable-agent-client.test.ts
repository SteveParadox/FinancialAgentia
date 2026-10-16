import { afterEach, expect, test } from 'bun:test';
import { cancelPendingResearch, cancelResearchExecution, executeDurableAgentQuery } from './durable-agent-client.js';

const initialFetch = globalThis.fetch;
function mockFetch(handler: (input: Parameters<typeof fetch>[0], init?: Parameters<typeof fetch>[1]) => Promise<Response>) {
  globalThis.fetch = Object.assign(handler, { preconnect: initialFetch.preconnect });
}
const initialBase = process.env.FINANCIALAGENTIA_BACKEND_URL;
const EXECUTION_ID = '00000000-0000-4000-8000-000000000012';
const SESSION_ID = '00000000-0000-4000-8000-000000000011';

afterEach(() => {
  globalThis.fetch = initialFetch;
  if (initialBase === undefined) delete process.env.FINANCIALAGENTIA_BACKEND_URL;
  else process.env.FINANCIALAGENTIA_BACKEND_URL = initialBase;
});

test('durable client submits idempotency key and accepts only validated terminal status', async () => {
  process.env.FINANCIALAGENTIA_BACKEND_URL = 'http://127.0.0.1:8000';
  const calls: Request[] = [];
  mockFetch(async (input, init) => {
    const request = new Request(input, init);
    calls.push(request);
    if (request.method === 'POST') {
      return Response.json({ execution_id: EXECUTION_ID, session_id: SESSION_ID, created: true, state: 'QUEUED' }, { status: 202 });
    }
    return Response.json({
      execution_id: EXECUTION_ID, session_id: SESSION_ID,
      state: 'SUCCEEDED', result: { schema_version: 1, execution_id: EXECUTION_ID, session_id: SESSION_ID,
        answer: 'Verified financial research', status: 'COMPLETE', claims: [], evidence: [], sources: [],
        limitations: [], agent_executions: [], researched_at: new Date().toISOString() },
    });
  });
  let seen: string | null = null;
  let idem: string | null = null;
  const answer = await executeDurableAgentQuery('AAPL', null, new AbortController().signal,
    executionId => { seen = executionId; },
    key => { idem = key; });
  expect(String(seen)).toBe(EXECUTION_ID);
  expect(answer.content).toBe('Verified financial research');
  expect(calls.length).toBe(2);
  expect(calls[0]?.headers.has('Idempotency-Key')).toBe(true);
  expect(calls[0]?.url.endsWith('/api/v1/agents/executions')).toBe(true);
  expect(idem).toBeTruthy();
});

test('invalid status or unsupported financial report is never displayed', async () => {
  mockFetch(async (input, init) => {
    const request = new Request(input, init);
    if (request.method === 'POST') {
      return Response.json({ execution_id: EXECUTION_ID, session_id: SESSION_ID, state: 'QUEUED' }, { status: 202 });
    }
    return Response.json({
      session_id: SESSION_ID, state: 'SUCCEEDED',
      result: { answer: 'An invented 999999 quote', status: 'UNVERIFIED' },
    });
  });
  await expect(executeDurableAgentQuery('AAPL', null, new AbortController().signal, () => {}))
    .rejects.toThrow('Invalid verified financial report');
});

test('client issues explicit cancellation including before execution ID is known', async () => {
  const calls: Request[] = [];
  mockFetch(async (input, init) => {
    calls.push(new Request(input, init));
    return Response.json({ state: 'CANCEL_PENDING', cancel_accepted: true });
  });
  await cancelPendingResearch('registered-idempotency-key');
  await cancelResearchExecution(EXECUTION_ID);
  expect(calls[0]?.headers.get('Idempotency-Key')).toBe('registered-idempotency-key');
  expect(calls[1]?.url.endsWith('/' + EXECUTION_ID + '/cancel')).toBe(true);
});

test('remote unauthenticated HTTP backend is prohibited', async () => {
  process.env.FINANCIALAGENTIA_BACKEND_URL = 'http://untrusted.example:8000';
  await expect(executeDurableAgentQuery('AAPL', null, new AbortController().signal, () => {}))
    .rejects.toThrow('HTTPS');
});

test('admission transport retry reuses the request payload and idempotency key', async () => {
  const requests: Request[] = [];
  mockFetch(async (input, init) => {
    const request = new Request(input, init);
    if (request.method === 'POST') {
      requests.push(request);
      if (requests.length === 1) throw new Error('Connection lost after admission');
      return Response.json({ execution_id: EXECUTION_ID, session_id: SESSION_ID, state: 'QUEUED' });
    }
    return Response.json({ state: 'FAILED', session_id: SESSION_ID });
  });
  await expect(executeDurableAgentQuery('AAPL', null, new AbortController().signal, () => {}))
    .rejects.toThrow('failed');
  expect(requests.length).toBe(2);
  expect(requests[0]!.headers.get('Idempotency-Key')).toBe(requests[1]!.headers.get('Idempotency-Key'));
  expect(await requests[0]!.text()).toBe(await requests[1]!.text());
});

test('streaming response budget is enforced even without a content length', async () => {
  mockFetch(async () => new Response(new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode('x'.repeat(512_001))); controller.close(); },
  })));
  await expect(executeDurableAgentQuery('AAPL', null, new AbortController().signal, () => {}))
    .rejects.toThrow('response too large');
});

test('model configuration comes from authenticated backend rather than local selection', async () => {
  const { getResearchModel } = await import('./durable-agent-client.js');
  mockFetch(async input => {
    expect(String(input)).toEndWith('/api/v1/model');
    return Response.json({ protocol_version: 1, provider: 'openai', model: 'openai/gpt-4.1', configured: true });
  });
  expect(await getResearchModel()).toEqual({ provider: 'openai', model: 'openai/gpt-4.1', configured: true });
});
