/** CLI transport for authoritative Python agent research. */
export interface AgentAnswer {
  content: string;
  session_id: string;
  request_id?: string;
}
const MAX_BYTES = 200_000;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function agentUrl(): string {
  const url = new URL(process.env.FINANCIALAGENTIA_BACKEND_URL || 'http://127.0.0.1:8000');
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password ||
      url.search || url.hash || url.pathname !== '/') {
    throw new Error('Invalid backend URL');
  }
  if (url.protocol === 'http:' && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) {
    throw new Error('Remote financial backends require HTTPS');
  }
  return new URL('/api/v1/agents/execute', url).toString();
}

function readFrame(frame: string): AgentAnswer | null {
  const data = frame.split('\n').filter(line => line.startsWith('data:'))
    .map(line => line.slice(5).trimStart()).join('\n');
  if (!data) return null;
  let value: unknown;
  try { value = JSON.parse(data); }
  catch { throw new Error('Malformed financial agent SSE event'); }
  if (!value || typeof value !== 'object') throw new Error('Invalid financial agent SSE event');
  const event = value as Record<string, unknown>;
  if (event.type === 'error') throw new Error(
    typeof event.error === 'string' ? event.error : 'Agent execution failed'
  );
  if (event.type !== 'answer') return null;
  if (typeof event.content !== 'string' || !event.content.trim() ||
      typeof event.session_id !== 'string' || !UUID.test(event.session_id)) {
    throw new Error('Invalid financial agent answer');
  }
  return {
    content: event.content,
    session_id: event.session_id,
    request_id: typeof event.request_id === 'string' ? event.request_id : undefined,
  };
}

export async function executeAgentQuery(
  query: string,
  sessionId: string | null,
  signal: AbortSignal,
): Promise<AgentAnswer> {
  if (!query.trim() || query.length > 10000) throw new Error('Invalid agent query');
  if (sessionId && !UUID.test(sessionId)) throw new Error('Invalid agent session');
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (process.env.BACKEND_API_KEY) headers['X-API-Key'] = process.env.BACKEND_API_KEY;
  if (process.env.AUTH_BEARER) headers.Authorization = 'Bearer ' + process.env.AUTH_BEARER;
  const timeout = AbortSignal.timeout(180_000);
  const combined = AbortSignal.any([signal, timeout]);
  const response = await fetch(agentUrl(), {
    method: 'POST', headers, signal: combined,
    body: JSON.stringify({ query, ...(sessionId ? { session_id: sessionId } : {}) }),
  });
  if (!response.ok) throw new Error('Financial agent backend HTTP ' + response.status);
  if (!response.body) throw new Error('Financial backend sent no response body');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let totalBytes = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      totalBytes += value.byteLength;
      if (totalBytes > MAX_BYTES) throw new Error('Financial agent response too large');
      buffer += decoder.decode(value, { stream: true }).replace(/\r/g, '');
      let boundary = buffer.indexOf('\n\n');
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const answer = readFrame(frame);
        if (answer) return answer;
        boundary = buffer.indexOf('\n\n');
      }
    }
    buffer += decoder.decode().replace(/\r/g, '');
    const final = readFrame(buffer);
    if (final) return final;
    throw new Error('Agent returned no terminal answer');
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
