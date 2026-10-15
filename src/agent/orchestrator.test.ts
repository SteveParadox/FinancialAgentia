import { afterEach, expect, test } from 'bun:test';
import { Agent } from './orchestrator.js';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

test('cancellation interrupts local transport before waiting for remote acknowledgement', async () => {
  const observed: { signal: AbortSignal | null } = { signal: null };
  let acknowledge: ((response: Response) => void) | undefined;
  const cancelReply = new Promise<Response>(resolve => { acknowledge = resolve; });
  globalThis.fetch = Object.assign(async (input: Parameters<typeof fetch>[0], init?: Parameters<typeof fetch>[1]) => {
    if (String(input).endsWith('/cancel-by-key')) return cancelReply;
    observed.signal = init?.signal ?? null;
    return await new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
    });
  }, { preconnect: originalFetch.preconnect });
  const agent = new Agent({ model: 'gpt-4o-mini' });
  const research = agent.run('AAPL').then(() => null, error => error);
  await Promise.resolve();
  const cancellation = agent.cancel();
  expect(observed.signal?.aborted).toBe(true);
  expect(await research).toBeInstanceOf(Error);
  acknowledge?.(Response.json({ cancel_accepted: true, state: 'CANCEL_PENDING' }));
  await cancellation;
});
