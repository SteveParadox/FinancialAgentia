/** Run against the isolated fixture server from test_runtime_roundtrip.py. */
import React from 'react';
import { render } from 'ink';
import { executeDurableAgentQuery, listResearchExecutions, waitForResearchExecution } from '../src/services/durable-agent-client.js';
import { AnswerBox } from '../src/components/AnswerBox.js';

console.error('E2E: submitting research');
const answer = await executeDurableAgentQuery('Research AAPL', null, new AbortController().signal, () => {});
if (!answer.content.includes('AAPL price: 100') || answer.content.includes('guaranteed')) throw new Error('Unexpected grounded answer');
console.error('E2E: retrieving history');
const history = await listResearchExecutions();
if (!history.some(item => item.execution_id === answer.execution_id)) throw new Error('History disconnected');
const resumed = await waitForResearchExecution(answer.execution_id, new AbortController().signal);
if (resumed.content !== answer.content) throw new Error('Report changed after reconnect');
const screen = render(<AnswerBox text={resumed.content} />, {
  stdout: process.stdout, stdin: process.stdin,
  stderr: process.stderr, debug: true, exitOnCtrlC: false, patchConsole: false,
});
await new Promise(resolve => setTimeout(resolve, 50));
const exited = screen.waitUntilExit();
screen.unmount();
await exited;
process.stdout.write('\n');
console.log(JSON.stringify({ execution_id: answer.execution_id, report_rendered: true, history_and_reconnect: true }));
