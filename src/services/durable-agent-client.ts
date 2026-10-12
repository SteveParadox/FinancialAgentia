/** Authoritative, idempotent durable financial research transport. */
export interface ResearchAnswer {
  content: string;
  session_id: string;
  execution_id: string;
  report: Record<string, unknown>;
}
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const TERMINAL = new Set(['SUCCEEDED', 'CANCELLED', 'FAILED', 'TIMED_OUT']);

function endpoint(path: string): URL {
  const base = new URL(process.env.FINANCIALAGENTIA_BACKEND_URL || 'http://127.0.0.1:8000');
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password ||
      base.search || base.hash || base.pathname !== '/') {
    throw new Error('Invalid financial backend URL');
  }
  if (base.protocol === 'http:' && !['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname)) {
    throw new Error('Remote research execution requires HTTPS');
  }
  return new URL(path, base);
}

function headers(): Record<string, string> {
  const result: Record<string, string> = { 'Content-Type': 'application/json' };
  if (process.env.BACKEND_API_KEY) result['X-API-Key'] = process.env.BACKEND_API_KEY;
  if (process.env.AUTH_BEARER) result.Authorization = 'Bearer ' + process.env.AUTH_BEARER;
  return result;
}

async function jsonResponse(response: Response): Promise<Record<string, unknown>> {
  if (!response.ok) throw new Error('Financial agent backend HTTP ' + response.status);
  const size = Number(response.headers.get('content-length') || '0');
  if (size > 512_000) throw new Error('Financial research response too large');
  const text = await response.text();
  if (text.length > 512_000) throw new Error('Financial research response too large');
  const decoded: unknown = JSON.parse(text);
  if (!decoded || typeof decoded !== 'object' || Array.isArray(decoded)) {
    throw new Error('Invalid financial research response');
  }
  return decoded as Record<string, unknown>;
}

export async function cancelPendingResearch(idempotencyKey: string): Promise<void> {
  const response = await fetch(endpoint('/api/v1/agents/executions/cancel-by-key'), {
    method: 'POST',
    headers: { ...headers(), 'Idempotency-Key': idempotencyKey },
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error('Research cancellation HTTP ' + response.status);
}

export async function cancelResearchExecution(executionId: string): Promise<void> {
  if (!UUID.test(executionId)) throw new Error('Invalid execution ID');
  const response = await fetch(endpoint('/api/v1/agents/executions/' + executionId + '/cancel'), {
    method: 'POST', headers: headers(), signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error('Research cancellation HTTP ' + response.status);
}

/** Admission retry reuses the exact idempotency key. */
export async function executeDurableAgentQuery(
  query: string, sessionId: string | null, signal: AbortSignal,
  onAdmitted: (executionId: string) => void,
  onIdempotencyKey?: (token: string) => void,
): Promise<ResearchAnswer> {
  if (!query.trim() || query.length > 10000) throw new Error('Invalid research query');
  if (sessionId !== null && !UUID.test(sessionId)) throw new Error('Invalid session ID');
  const idem = crypto.randomUUID();
  onIdempotencyKey?.(idem);
  const joined = AbortSignal.any([signal, AbortSignal.timeout(360_000)]);
  const body = JSON.stringify({ query, ...(sessionId ? { session_id: sessionId } : {}) });
  let admitted: Record<string, unknown> | undefined;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const response = await fetch(endpoint('/api/v1/agents/executions'), {
        method: 'POST', headers: { ...headers(), 'Idempotency-Key': idem },
        signal: joined, body,
      });
      admitted = await jsonResponse(response);
      break;
    } catch (error) {
      if (attempt || joined.aborted) throw error;
      await new Promise<void>(resolve => setTimeout(resolve, 250));
    }
  }
  if (!admitted || typeof admitted.execution_id !== 'string' ||
      !UUID.test(admitted.execution_id) || typeof admitted.session_id !== 'string' ||
      !UUID.test(admitted.session_id)) {
    throw new Error('Invalid durable research admission');
  }
  const executionId = admitted.execution_id;
  const acceptedSession = admitted.session_id;
  onAdmitted(executionId);
  const pollUrl = endpoint('/api/v1/agents/executions/' + executionId);
  for (;;) {
    if (joined.aborted) throw new Error('Financial research interrupted');
    const state = await jsonResponse(await fetch(pollUrl, {
      method: 'GET', headers: headers(), signal: joined,
    }));
    if (typeof state.state !== 'string' || typeof state.session_id !== 'string' ||
        state.session_id !== acceptedSession) {
      throw new Error('Invalid financial execution status');
    }
    if (TERMINAL.has(state.state)) {
      if (state.state !== 'SUCCEEDED') {
        throw new Error('Research execution ' + state.state.toLowerCase());
      }
      const report = state.result;
      if (!report || typeof report !== 'object' || Array.isArray(report)) {
        throw new Error('Missing validated financial report');
      }
      const parsed = report as Record<string, unknown>;
      if (typeof parsed.answer !== 'string' || !parsed.answer.trim() ||
          !['COMPLETE', 'PARTIAL', 'INSUFFICIENT_EVIDENCE'].includes(String(parsed.status))) {
        throw new Error('Invalid verified financial report');
      }
      return { content: parsed.answer, session_id: acceptedSession,
               execution_id: executionId, report: parsed };
    }
    if (!['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(state.state)) {
      throw new Error('Unknown research execution status');
    }
    await new Promise<void>(resolve => setTimeout(resolve, 600));
  }
}
