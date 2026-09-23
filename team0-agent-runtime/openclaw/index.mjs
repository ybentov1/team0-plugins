import { definePluginEntry } from 'openclaw/plugin-sdk/plugin-entry';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createOpenClawAdapter, runRuntimeHook } from './adapter.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

export default definePluginEntry({
  id: 'team0-agent-runtime',
  name: 'Team0 Living Understanding',
  description: 'Read Team0 before OpenClaw turns and contribute completed turns under the owner grant.',
  register(api) {
    const adapter = createOpenClawAdapter({
      getConfig: () => api.runtime.config.current(),
      invoke: (command, event, connection) => runRuntimeHook({
        root, command, event, connection, timeoutMs: command === 'after-turn' ? 25000 : 12000,
      }),
      logger: api.logger,
    });
    api.on('before_prompt_build', adapter.beforePromptBuild);
    api.on('agent_end', adapter.agentEnd);
  },
});
