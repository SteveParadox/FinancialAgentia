import { afterEach, expect, test } from 'bun:test';
import { cancelPendingResearch, cancelResearchExecution, executeDurableAgentQuery } from './durable-agent-client.js';

const initialFetch = globalThis.fetch;
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
  globalThis.fetch = async (input, init) => {
    const request = new Request(input, init);
    calls.push(request);
    if (request.method === 'POST') {
      return Response.json({ execution_id: EXECUTION_ID, session_id: SESSION_ID, created: true, state: 'QUEUED' }, { status: 202 });
    }
    return Response.json({
      execution_id: EXECUTION_ID, session_id: SESSION_ID,
      state: 'SUCCEEDED', result: { answer: 'Verified financial research', status: 'COMPLETE' },
    });
  };
  let seen: string | null = null;
  let idem: string | null = null;
  const answer = await executeDurableAgentQuery('AAPL', null, new AbortController().signal,
    executionId => { seen = executionId; },
    key => { idem = key; });
  expect(seen).toBe(EXECUTION_ID);
  expect(answer.content).toBe('Verified financial research');
  expect(calls.length).toBe(2);
  expect(calls[0]?.headers.has('Idempotency-Key')).toBe(true);
  expect(calls[0]?.url.endsWith('/api/v1/agents/executions')).toBe(true);
  expect(idem).toBeTruthy();
});

test('invalid status or unsupported financial report is never displayed', async () => {
  globalThis.fetch = async (input, init) => {
    const request = new Request(input, init);
    if (request.method === 'POST') {
      return Response.json({ execution_id: EXECUTION_ID, session_id: SESSION_ID, state: 'QUEUED' }, { status: 202 });
    }
    return Response.json({
      session_id: SESSION_ID, state: 'SUCCEEDED',
      result: { answer: 'An invented 999999 quote', status: 'UNVERIFIED' },
    });
  };
  await expect(executeDurableAgentQuery('AAPL', null, new AbortController().signal, () => {}))
    .rejects.toThrow('Invalid verified financial report');
});

test('client issues explicit cancellation including before execution ID is known', async () => {
  const calls: Request[] = [];
  globalThis.fetch = async (input, init) => {
    calls.push(new Request(input, init));
    return Response.json({ state: 'CANCEL_PENDING', cancel_accepted: true });
  };
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
