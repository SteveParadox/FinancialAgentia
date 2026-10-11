/** Single authoritative Python agent API contract for the Bun CLI. */
export interface AgentAnswer {
  content: string;
  session_id: string;
  request_id?: string;
}

export async function executeAgentQuery(
  query: string,
  sessionId: string | null,
  signal: AbortSignal,
): Promise<AgentAnswer> {
  const env = process.env;
  const base = (env.FINANCIALAGENTIA_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '');
  const url = new URL(base);
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error('Invalid backend URL scheme');
  }
  if (url.protocol === 'http:' && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
      && (env.BACKEND_API_KEY || env.AUTH_BEARER)) {
    throw new Error('Authenticated remote backends require HTTPS');
  }
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (env.BACKEND_API_KEY) headers['X-API-Key'] = env.BACKEND_API_KEY;
  if (env.AUTH_BEARER) headers.Authorization = 'Bearer ' + env.AUTH_BEARER;
  const response = await fetch(base + '/api/v1/agents/execute', {
    method: 'POST', headers, signal,
    body: JSON.stringify({ query, ...(sessionId ? { session_id: sessionId } : {}) }),
  });
  if (!response.ok) throw new Error('Financial agent backend HTTP ' + response.status);
  const rawBody = await response.text();
  if (rawBody.length > 200_000) throw new Error('Agent response exceeded size limit');
  const body = rawBody.replace(/\r\n/g, '\n');
  for (const frame of body.split('\n\n')) {
    const data = frame.split('\n').filter(line => line.startsWith('data:'))
      .map(line => line.slice(5).trimStart()).join('\n');
    if (!data) continue;
    let event: unknown;
    try { event = JSON.parse(data); }
    catch { throw new Error('Invalid agent event format'); }
    if (!event || typeof event !== 'object') throw new Error('Invalid agent event');
    const item = event as Record<string, unknown>;
    if (item.type === 'error') throw new Error(
      typeof item.error === 'string' ? item.error : 'Agent execution failed'
    );
    if (item.type === 'answer') {
      if (typeof item.content !== 'string' || !item.content.trim()
          || typeof item.session_id !== 'string') {
        throw new Error('Invalid agent answer contract');
      }
      return {
        content: item.content, session_id: item.session_id,
        request_id: typeof item.request_id === 'string' ? item.request_id : undefined,
      };
    }
  }
  throw new Error('Agent backend returned no answer event');
}
