import { spawn } from 'node:child_process';
import { homedir } from 'node:os';
import { join } from 'node:path';

const TEAM0_MCP_PATH = '/v1/mcp';

export function connectionFromConfig(config, env = process.env) {
  const server = config?.mcp?.servers?.team0;
  if (!server || server.enabled === false || typeof server.url !== 'string') return null;
  let url;
  try { url = new URL(server.url); } catch { return null; }
  const loopback = url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if (url.pathname !== TEAM0_MCP_PATH || (url.origin !== 'https://api.team0.ai' && !loopback)) return null;
  const authorization = server.headers?.Authorization || server.headers?.authorization;
  const match = typeof authorization === 'string' ? /^Bearer\s+(.+)$/i.exec(authorization.trim()) : null;
  const key = match?.[1] || env.TEAM0_API_KEY;
  return key ? { key, baseUrl: `${url.origin}/v1` } : null;
}

export function visibleText(content) {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content.filter((part) => part?.type === 'text' && typeof part.text === 'string')
    .map((part) => part.text).join('');
}

export function runRuntimeHook({ root, command, event, connection, timeoutMs = 12000 }) {
  return new Promise((resolve, reject) => {
    const child = spawn('python3', [join(root, 'scripts/team0_hook.py'), command], {
      env: {
        ...process.env,
        PLUGIN_ROOT: root,
        TEAM0_RUNTIME_HOST_ID: 'openclaw',
        TEAM0_RUNTIME_DATA_DIR: join(process.env.OPENCLAW_STATE_DIR || join(homedir(), '.openclaw'), 'team0-agent-runtime'),
        TEAM0_API_KEY: connection.key,
        TEAM0_API_BASE_URL: connection.baseUrl,
        TEAM0_CONTRIBUTION_SOURCE_ID: '',
        TEAM0_AGENT_SOURCE_ID: '',
        TEAM0_RUNTIME_ALLOW_INSECURE_LOCAL: connection.baseUrl.startsWith('http://') ? 'true' : 'false',
        TEAM0_RUNTIME_SURFACE_ERRORS: 'false',
      },
      stdio: ['pipe', 'pipe', 'ignore'],
    });
    let output = '';
    const timer = setTimeout(() => child.kill(), timeoutMs);
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk) => {
      output += chunk;
      if (output.length > 200_000) child.kill();
    });
    child.on('error', (error) => { clearTimeout(timer); reject(error); });
    child.stdin.on('error', () => { /* A child that exits early is handled by close. */ });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code !== 0 || output.length > 200_000) return reject(new Error('Team0 runtime hook failed'));
      try { resolve(JSON.parse(output)); } catch { reject(new Error('Invalid Team0 runtime hook response')); }
    });
    child.stdin.end(JSON.stringify(event));
  });
}

export function createOpenClawAdapter({ getConfig, invoke, logger }) {
  const pending = new Map();
  const sessionPending = new Map();

  async function beforePromptBuild(event, ctx) {
    if (ctx.inputProvenance?.kind !== 'external_user') return;
    if (typeof event.currentUserMessage !== 'string' || !event.currentUserMessage.trim()) return;
    const connection = connectionFromConfig(getConfig());
    if (!connection) return;
    const sessionId = ctx.sessionKey || ctx.sessionId;
    const turnId = event.currentUserMessageId || ctx.runId;
    if (!sessionId || !turnId) return;
    const prior = pending.get(ctx.runId) || sessionPending.get(sessionId);
    if (prior?.turnId === turnId) return prior.injection;
    try {
      const result = await invoke('before-turn', {
        session_id: sessionId, turn_id: turnId, prompt: event.currentUserMessage,
      }, connection);
      ctx.hookInvocation?.assertActive?.();
      const context = result?.hookSpecificOutput?.additionalContext;
      const injection = typeof context === 'string' && context
        ? { prependSystemContext: `${context}\n\nTeam0's OpenClaw runtime returns this completed turn automatically. Do not call team0_contribute_conversation for this turn.` }
        : undefined;
      const state = { sessionId, turnId, prompt: event.currentUserMessage, connection, injection };
      if (ctx.runId) pending.set(ctx.runId, state);
      sessionPending.set(sessionId, state);
      return injection;
    } catch (error) {
      logger?.warn?.(`Team0 before-turn hook skipped: ${error.name || 'error'}`);
    }
  }

  async function agentEnd(event, ctx) {
    const runId = ctx.runId || event.runId;
    const runState = runId ? pending.get(runId) : null;
    const state = runId ? runState : sessionPending.get(ctx.sessionKey);
    if (!state) return;
    if (runId) pending.delete(runId);
    if (sessionPending.get(state.sessionId) === state) sessionPending.delete(state.sessionId);
    if (event.success === false) return;
    const messages = event.messages || [];
    const userIndex = messages.findLastIndex((message) =>
      message?.role === 'user' && visibleText(message.content) === state.prompt);
    // Some runners emit only final assistant messages. A matching run ID still
    // binds those messages to the exact user input captured before this run.
    if (userIndex < 0 && !runState) return;
    const lastAssistant = messages.slice(userIndex + 1).reverse()
      .find((message) => message?.role === 'assistant' && visibleText(message.content).trim());
    const answer = visibleText(lastAssistant?.content);
    if (!answer.trim()) return;
    try {
      await invoke('after-turn', {
        session_id: state.sessionId, turn_id: state.turnId, last_assistant_message: answer,
      }, state.connection);
    } catch (error) {
      logger?.warn?.(`Team0 after-turn hook skipped: ${error.name || 'error'}`);
    }
  }

  return { beforePromptBuild, agentEnd };
}
