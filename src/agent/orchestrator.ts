/** Compatibility API: Python is the only research executor. */
import { executeDurableAgentQuery, cancelResearchExecution, cancelPendingResearch } from '../services/durable-agent-client.js';
import { MessageHistory } from '../utils/message-history.js';
import type { TaskExecutorCallbacks } from './task-executor.js';
import type { Phase, Plan, Understanding, ReflectionResult } from './state.js';

export interface AgentCallbacks extends TaskExecutorCallbacks {
  // Phase transitions
  onPhaseStart?: (phase: Phase) => void;
  onPhaseComplete?: (phase: Phase) => void;

  // Understanding
  onUnderstandingComplete?: (understanding: Understanding) => void;

  // Planning
  onPlanCreated?: (plan: Plan, iteration: number) => void;

  // Reflection
  onReflectionComplete?: (result: ReflectionResult, iteration: number) => void;
  onIterationStart?: (iteration: number) => void;

  // Answer
  onAnswerStart?: () => void;
  onAnswerStream?: (stream: AsyncGenerator<string>) => void;
}

// ============================================================================
// Agent Options
// ============================================================================

export interface AgentOptions {
  model: string;
  callbacks?: AgentCallbacks;
  maxIterations?: number;
}


export class Agent {
  private sessionId: string | null = null;
  private executionId: string | null = null;
  private idempotencyKey: string | null = null;
  private controller: AbortController | null = null;
  private readonly history: MessageHistory;
  
  constructor(private readonly options: AgentOptions) {
    this.history = new MessageHistory(options.model);
  }

  async run(query: string, _history?: MessageHistory): Promise<string> {
    if (this.controller) throw new Error('Research already running');
    const controller = new AbortController();
    this.controller = controller;
    try {
      const answer = await executeDurableAgentQuery(query, this.sessionId, controller.signal,
        id => { this.executionId = id; }, key => { this.idempotencyKey = key; },
        (_state, phase) => {
          if (phase && ['understand', 'plan', 'execute', 'reflect', 'answer'].includes(phase)) {
            this.options.callbacks?.onPhaseStart?.(phase as Phase);
          }
        });
      this.sessionId = answer.session_id;
      this.options.callbacks?.onAnswerStart?.();
      this.options.callbacks?.onAnswerStream?.((async function* () { yield answer.content; })());
      this.options.callbacks?.onPhaseComplete?.('answer');
      return answer.content;
    } finally {
      this.controller = null;
      this.executionId = null;
      this.idempotencyKey = null;
    }
  }

  async cancel(): Promise<void> {
    if (this.executionId) await cancelResearchExecution(this.executionId);
    else if (this.idempotencyKey) await cancelPendingResearch(this.idempotencyKey);
    this.controller?.abort();
  }

  /** Legacy local view; durable conversation truth lives on the backend. */
  getMessageHistory(): MessageHistory { return this.history; }
}
