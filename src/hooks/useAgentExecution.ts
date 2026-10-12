import { useCallback, useRef, useState } from 'react';
import { executeDurableAgentQuery, cancelResearchExecution, cancelPendingResearch } from '../services/durable-agent-client.js';
import { generateId } from '../cli/types.js';
import type { MessageHistory } from '../utils/message-history.js';
import type { AgentProgressState } from '../components/AgentProgressView.js';

export interface CurrentTurn {
  id: string;
  query: string;
  state: AgentProgressState;
}
export interface ToolError {
  taskId: string;
  toolName: string;
  args: Record<string, unknown>;
  error: string;
}
interface SSETokenEvent {
  token: string;
  role?: string;
  request_id?: string;
}

/** Only Python owns research, tool execution and conversation history. */
export function useAgentExecution(_options: { model: string; messageHistory: MessageHistory }) {
  const [currentTurn, setCurrentTurn] = useState<CurrentTurn | null>(null);
  const [answerStream, setAnswerStream] = useState<AsyncGenerator<SSETokenEvent> | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const isProcessingRef = useRef(false);
  const controllerRef = useRef<AbortController | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const executionIdRef = useRef<string | null>(null);
  const idempotencyKeyRef = useRef<string | null>(null);
  const requestGenerationRef = useRef(0);

  const processQuery = useCallback(async (query: string): Promise<void> => {
    if (isProcessingRef.current) return;
    isProcessingRef.current = true;
    setIsProcessing(true);
    const generation = ++requestGenerationRef.current;
    const controller = new AbortController();
    controllerRef.current = controller;
    executionIdRef.current = null;
    idempotencyKeyRef.current = null;
    setAnswerStream(null);
    setCurrentTurn({
      id: generateId(),
      query,
      state: {
        currentPhase: 'understand',
        understandComplete: false,
        planComplete: false,
        reflectComplete: false,
        tasks: [],
        isAnswering: false,
        researchingBackend: true,
      },
    });
    try {
      const answer = await executeDurableAgentQuery(
        query, sessionIdRef.current, controller.signal,
        (executionId: string) => {
          if (generation === requestGenerationRef.current && !controller.signal.aborted) {
            executionIdRef.current = executionId;
          } else {
            void cancelResearchExecution(executionId).catch(() => undefined);
          }
        },
        (token: string) => {
          if (generation === requestGenerationRef.current && !controller.signal.aborted) {
            idempotencyKeyRef.current = token;
          } else {
            void cancelPendingResearch(token).catch(() => undefined);
          }
        },
        (_state: string, phase: string | null) => {
          if (generation !== requestGenerationRef.current || controller.signal.aborted || !phase) return;
          const order = ['understand', 'plan', 'execute', 'reflect', 'answer', 'validate'];
          const position = order.indexOf(phase);
          if (position < 0) return;
          const shown = phase === 'validate' ? 'reflect' : phase;
          setCurrentTurn(prev => prev ? {
            ...prev,
            state: {
              ...prev.state,
              currentPhase: shown as AgentProgressState['currentPhase'],
              understandComplete: position > 0,
              planComplete: position > 1,
              reflectComplete: position > 3,
              researchingBackend: true,
            },
          } : prev);
        },
      );
      if (controller.signal.aborted || generation !== requestGenerationRef.current) {
        throw new Error('Agent execution interrupted');
      }
      sessionIdRef.current = answer.session_id;
      executionIdRef.current = null;
      idempotencyKeyRef.current = null;
      setCurrentTurn(prev => prev?.query === query ? {
        ...prev,
        state: { ...prev.state, researchingBackend: false, isAnswering: false },
      } : prev);
      setAnswerStream((async function* () {
        yield { token: answer.content, request_id: answer.execution_id };
      })());
    } catch (error) {
      if (generation === requestGenerationRef.current) setCurrentTurn(null);
      if (controller.signal.aborted) throw new Error('Agent execution interrupted');
      throw error;
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
      if (generation === requestGenerationRef.current) {
        isProcessingRef.current = false;
        setIsProcessing(false);
      }
    }
  }, []);

  const handleAnswerComplete = useCallback((_answer: string) => {
    setCurrentTurn(null);
    setAnswerStream(null);
  }, []);

  const cancelExecution = useCallback(() => {
    requestGenerationRef.current += 1;
    const executionId = executionIdRef.current;
    const idempotencyKey = idempotencyKeyRef.current;
    executionIdRef.current = null;
    idempotencyKeyRef.current = null;
    controllerRef.current?.abort();
    if (executionId) {
      void cancelResearchExecution(executionId).catch(() => undefined);
    } else if (idempotencyKey) {
      void cancelPendingResearch(idempotencyKey).catch(() => undefined);
    }
    controllerRef.current = null;
    isProcessingRef.current = false;
    setIsProcessing(false);
    setCurrentTurn(null);
    setAnswerStream(null);
  }, []);

  return {
    currentTurn, answerStream, isProcessing,
    toolErrors: [] as ToolError[],
    processQuery, handleAnswerComplete, cancelExecution,
  };
}
