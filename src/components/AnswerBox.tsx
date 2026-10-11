import React, { useState, useEffect, useRef } from 'react';
import { Box, Text } from 'ink';
import { colors } from '../theme.js';

interface SSETokenEvent {
  token: string;
  role?: string;
  request_id?: string;
}

interface AnswerBoxProps {
  stream?: AsyncGenerator<SSETokenEvent>;
  text?: string;
  onStart?: () => void;
  onComplete?: (answer: string) => void;
}

export const AnswerBox = React.memo(function AnswerBox({ stream, text, onStart, onComplete }: AnswerBoxProps) {
  const [content, setContent] = useState(text || '');
  const [isStreaming, setIsStreaming] = useState(!!stream);

  // Store callbacks in refs to avoid effect re-runs when references change
  const onStartRef = useRef(onStart);
  const onCompleteRef = useRef(onComplete);
  onStartRef.current = onStart;
  onCompleteRef.current = onComplete;

  useEffect(() => {
    if (!stream) return;
    let cancelled = false;
    let collected = text || '';
    let started = false;
    (async () => {
      let completed = false;
      try {
        for await (const chunk of stream) {
          if (cancelled) break;
          if (!started && chunk.token.trim()) {
            started = true;
            onStartRef.current?.();
          }
          collected += chunk.token;
          if (!cancelled) setContent(collected);
        }
        completed = true;
      } catch {
        // Do not persist incomplete responses as completed research.
      } finally {
        if (!cancelled) {
          setIsStreaming(false);
          if (completed) onCompleteRef.current?.(collected);
        }
      }
    })();
    return () => {
      cancelled = true;
      void stream.return?.(undefined).catch(() => undefined);
    };
  }, [stream, text]);

  return (
    <Box flexDirection="column" marginTop={1}>
      <Text>
        {content}
        {isStreaming && '▌'}
      </Text>
    </Box>
  );
});

interface UserQueryProps {
  query: string;
}

export function UserQuery({ query }: UserQueryProps) {
  return (
    <Box marginTop={1} paddingRight={2}>
      <Text color={colors.white} backgroundColor={colors.mutedDark}>
        {'>'} {query}{' '}
      </Text>
    </Box>
  );
}
