import assert from 'node:assert/strict';
import test from 'node:test';
import { connectionFromConfig, createOpenClawAdapter, visibleText } from './adapter.mjs';

const config = { mcp: { servers: { team0: {
  url: 'https://api.team0.ai/v1/mcp', headers: { Authorization: 'Bearer test-key' },
} } } };

test('uses only the configured Team0 MCP grant and never returns its key as context', () => {
  assert.deepEqual(connectionFromConfig(config), { key: 'test-key', baseUrl: 'https://api.team0.ai/v1' });
  assert.equal(connectionFromConfig({ mcp: { servers: { team0: { ...config.mcp.servers.team0, enabled: false } } } }), null);
  assert.equal(connectionFromConfig({ mcp: { servers: { team0: { url: 'https://example.com/v1/mcp', headers: {} } } } }, {}), null);
  assert.equal(connectionFromConfig({ mcp: { servers: { team0: { url: 'https://example.com/v1/mcp', headers: { Authorization: 'Bearer key' } } } } }), null);
  assert.equal(connectionFromConfig({ mcp: { servers: { team0: { url: 'https://api.team0.ai/other', headers: { Authorization: 'Bearer key' } } } } }), null);
});

test('keeps thinking and tool blocks out of the contributed assistant text', () => {
  assert.equal(visibleText([{ type: 'thinking', thinking: 'secret' }, { type: 'text', text: 'Answer' }, { type: 'tool_use', name: 'x' }]), 'Answer');
});

test('automatically reads before and contributes the exact successful turn once', async () => {
  const calls = [];
  const adapter = createOpenClawAdapter({
    getConfig: () => config,
    invoke: async (command, event, connection) => {
      calls.push({ command, event, connection });
      return command === 'before-turn'
        ? { hookSpecificOutput: { additionalContext: 'Team0 context' } }
        : {};
    },
  });
  const ctx = { sessionKey: 'agent:claw:session', runId: 'run-1', inputProvenance: { kind: 'external_user' } };
  const request = { prompt: 'I chose Thursday.', messages: [] };
  const injected = await adapter.beforePromptBuild(request, ctx);
  assert.match(injected.prependSystemContext, /Team0 context/);
  assert.doesNotMatch(injected.prependSystemContext, /test-key/);
  await adapter.beforePromptBuild(request, ctx);
  await adapter.agentEnd({ success: true, messages: [
    { role: 'user', content: 'I chose Thursday.' },
    { role: 'assistant', content: [{ type: 'thinking', thinking: 'hidden' }, { type: 'text', text: 'Thursday is the plan.' }] },
  ] }, ctx);
  await adapter.agentEnd({ success: true, messages: [] }, ctx);
  assert.equal(calls.length, 2);
  assert.deepEqual(calls.map((call) => call.command), ['before-turn', 'after-turn']);
  assert.equal(calls[0].event.prompt, 'I chose Thursday.');
  assert.equal(calls[1].event.last_assistant_message, 'Thursday is the plan.');
  assert.equal(calls[1].event.turn_id, 'run-1');
});

test('reads and returns direct webchat turns without a provenance field', async () => {
  const calls = [];
  const adapter = createOpenClawAdapter({
    getConfig: () => config,
    invoke: async (command, event) => {
      calls.push({ command, event });
      return command === 'before-turn'
        ? { hookSpecificOutput: { additionalContext: 'Team0 context' } }
        : {};
    },
  });
  const ctx = { sessionKey: 'webchat:session', runId: 'webchat-run', channel: 'webchat', messageProvider: 'webchat' };
  const injected = await adapter.beforePromptBuild({ prompt: 'A direct message', messages: [] }, ctx);
  assert.match(injected.prependSystemContext, /Team0 context/);
  await adapter.agentEnd({ success: true, messages: [
    { role: 'user', content: 'A direct message' },
    { role: 'assistant', content: 'A direct answer' },
  ] }, ctx);
  assert.deepEqual(calls.map((call) => call.command), ['before-turn', 'after-turn']);
  assert.equal(calls[1].event.turn_id, 'webchat-run');
});

test('does not send internal, failed, or unmatched turns to Team0', async () => {
  const calls = [];
  const adapter = createOpenClawAdapter({
    getConfig: () => config,
    invoke: async (command) => { calls.push(command); return {}; },
  });
  const ctx = { sessionKey: 'session', runId: 'run-1', inputProvenance: { kind: 'external_user' } };
  await adapter.beforePromptBuild({ prompt: 'system' }, { ...ctx, inputProvenance: { kind: 'internal_system' } });
  await adapter.beforePromptBuild({ prompt: 'unknown origin' }, { ...ctx, inputProvenance: undefined });
  await adapter.beforePromptBuild({ prompt: 'internal webchat' }, {
    ...ctx, inputProvenance: undefined, channel: 'webchat', messageProvider: 'webchat', trigger: 'heartbeat',
  });
  assert.equal(calls.length, 0);
  await adapter.beforePromptBuild({ prompt: 'hello' }, ctx);
  await adapter.agentEnd({ success: false, messages: [{ role: 'assistant', content: 'old answer' }] }, ctx);
  await adapter.beforePromptBuild({ prompt: 'hello again' }, ctx);
  await adapter.agentEnd({ success: true, messages: [{ role: 'assistant', content: 'old answer' }] }, { ...ctx, runId: 'other-run' });
  assert.deepEqual(calls, ['before-turn', 'before-turn']);
});

test('accepts assistant-only final messages only when the run ID matches', async () => {
  const calls = [];
  const adapter = createOpenClawAdapter({
    getConfig: () => config,
    invoke: async (command, event) => { calls.push({ command, event }); return {}; },
  });
  const ctx = { sessionKey: 'session', runId: 'run-2', inputProvenance: { kind: 'external_user' } };
  await adapter.beforePromptBuild({ prompt: 'A new decision' }, ctx);
  await adapter.agentEnd({ success: true, messages: [{ role: 'assistant', content: 'Acknowledged' }] }, ctx);
  assert.equal(calls[1].event.last_assistant_message, 'Acknowledged');
  assert.equal(calls[1].event.turn_id, 'run-2');
});
