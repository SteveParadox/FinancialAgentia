import { afterEach, expect, test } from 'bun:test';
import { executeAgentQuery } from './agent-client.js';

const realFetch = globalThis.fetch;
function mockFetch(handler: (input: Parameters<typeof fetch>[0], init?: Parameters<typeof fetch>[1]) => Promise<Response>) {
  globalThis.fetch = Object.assign(handler, { preconnect: realFetch.preconnect });
}
const oldBase = process.env.FINANCIALAGENTIA_BACKEND_URL;
const oldApiKey = process.env.BACKEND_API_KEY;
afterEach(() => {
  globalThis.fetch = realFetch;
  if (oldBase === undefined) delete process.env.FINANCIALAGENTIA_BACKEND_URL;
  else process.env.FINANCIALAGENTIA_BACKEND_URL = oldBase;
  if (oldApiKey === undefined) delete process.env.BACKEND_API_KEY;
  else process.env.BACKEND_API_KEY = oldApiKey;
});

test('CLI invokes Python agent and carries session ID', async () => {
  process.env.FINANCIALAGENTIA_BACKEND_URL = 'http://127.0.0.1:8000';
  process.env.BACKEND_API_KEY = 'test-key';
  let captured: Request | undefined;
  mockFetch(async (input, init) => {
    captured = new Request(input, init);
    return new Response('data: {"type":"answer","content":"Retrieved evidence","session_id":"00000000-0000-4000-8000-000000000001"}\n\n');
  });
  const result = await executeAgentQuery('Research AAPL', '00000000-0000-4000-8000-000000000001', new AbortController().signal);
  expect(result.content).toBe('Retrieved evidence');
  expect(captured?.url).toBe('http://127.0.0.1:8000/api/v1/agents/execute');
  expect(captured?.headers.get('x-api-key')).toBe('test-key');
  expect(await captured?.json()).toEqual({ query: 'Research AAPL', session_id: '00000000-0000-4000-8000-000000000001' });
});

test('SSE provider execution errors reject the CLI request', async () => {
  mockFetch(async () => new Response('data: {"type":"error","error":"Agent execution failed"}\n\n'));
  await expect(executeAgentQuery('AAPL', null, new AbortController().signal)).rejects.toThrow('Agent execution failed');
});

test('authenticated remote HTTP is rejected', async () => {
  process.env.BACKEND_API_KEY = 'test-key';
  process.env.FINANCIALAGENTIA_BACKEND_URL = 'http://example.org:8000';
  await expect(executeAgentQuery('AAPL', null, new AbortController().signal)).rejects.toThrow('HTTPS');
});

test('fragmented SSE with progress events returns only the verified terminal answer', async () => {
  process.env.FINANCIALAGENTIA_BACKEND_URL = 'http://127.0.0.1:8000';
  const chunks = [
    'data: {"type":"progress"}\\n\\n'.replace(/\\n/g, '\n'),
    'data: {"type":"answer","content":"Source verified","session_id":"00000000-0000-4000-8000-000000000001"}\n',
    '\n',
  ];
  mockFetch(async () => new Response(new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
      controller.close();
    },
  })));
  const answer = await executeAgentQuery('AAPL', null, new AbortController().signal);
  expect(answer.content).toBe('Source verified');
});

test('missing terminal agent answer is not reported as successful', async () => {
  mockFetch(async () => new Response('data: {"type":"progress"}\n\n'));
  await expect(executeAgentQuery('AAPL', null, new AbortController().signal))
    .rejects.toThrow('no terminal answer');
});

test('oversized backend event data is rejected without accumulating unbounded memory', async () => {
  mockFetch(async () => new Response('X'.repeat(200_001)));
  await expect(executeAgentQuery('AAPL', null, new AbortController().signal))
    .rejects.toThrow('response too large');
});
