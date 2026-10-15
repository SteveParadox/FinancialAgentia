import { ResearchReportSchema } from "./research-contract.js";

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
  if (!response.body) throw new Error('Missing research response body');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let text = '';
  let bytes = 0;
  try {
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      bytes += chunk.value.byteLength;
      if (bytes > 512_000) throw new Error('Financial research response too large');
      text += decoder.decode(chunk.value, { stream: true });
    }
    text += decoder.decode();
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
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
  onStatus?: (state: string, phase: string | null) => void,
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
        signal: AbortSignal.any([joined, AbortSignal.timeout(10_000)]), body,
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
  return waitForResearchExecution(executionId, joined, onStatus, acceptedSession);
}

export async function getResearchModel(): Promise<{ model: string; provider: string; configured: boolean }> {
  const response = await jsonResponse(await fetch(endpoint('/api/v1/model'), {
    headers: headers(), signal: AbortSignal.timeout(10_000),
  }));
  if (response.protocol_version !== 1 || typeof response.model !== 'string' ||
      typeof response.provider !== 'string' || typeof response.configured !== 'boolean') {
    throw new Error('Invalid backend model configuration');
  }
  return { model: response.model, provider: response.provider, configured: response.configured };
}

export async function listResearchExecutions(): Promise<Record<string, unknown>[]> {
  const response = await jsonResponse(await fetch(endpoint('/api/v1/agents/executions'), {
    headers: headers(), signal: AbortSignal.timeout(10_000),
  }));
  if (!Array.isArray(response.executions)) throw new Error('Invalid research history');
  return response.executions as Record<string, unknown>[];
}

export async function waitForResearchExecution(
  executionId: string, signal: AbortSignal,
  onStatus?: (state: string, phase: string | null) => void,
  expectedSession?: string,
): Promise<ResearchAnswer> {
  if (!UUID.test(executionId)) throw new Error('Invalid execution ID');
  const joined = AbortSignal.any([signal, AbortSignal.timeout(360_000)]);
  let acceptedSession = expectedSession;
  const pollUrl = endpoint('/api/v1/agents/executions/' + executionId);
  let failures = 0;
  for (;;) {
    if (joined.aborted) throw new Error('Financial research interrupted');
    let state: Record<string, unknown>;
    try {
      state = await jsonResponse(await fetch(pollUrl, {
        method: 'GET', headers: headers(), signal: AbortSignal.any([joined, AbortSignal.timeout(10_000)]),
      }));
      failures = 0;
    } catch (error) {
      if (joined.aborted || ++failures >= 4) throw error;
      await new Promise<void>(resolve => setTimeout(resolve, 500 * failures));
      continue;
    }
    if (typeof state.state !== 'string' || typeof state.session_id !== 'string' ||
        !UUID.test(state.session_id) || (acceptedSession && state.session_id !== acceptedSession)) {
      throw new Error('Invalid financial execution status');
    }
    acceptedSession = state.session_id;
    onStatus?.(state.state, typeof state.current_phase === 'string' ? state.current_phase : null);
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
      const verified = ResearchReportSchema.safeParse(parsed);
      if (!verified.success || verified.data.execution_id !== executionId || verified.data.session_id !== acceptedSession) {
        throw new Error('Invalid verified financial report contract');
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
