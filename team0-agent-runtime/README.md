# Team0 Living Understanding for connected agents

This plugin packages adapters for the host-neutral Team0 Agent Runtime. It uses one Team0
agent grant and one access key for two complementary jobs:

1. Lifecycle hooks automatically retrieve relevant Living Understanding before every connected-agent turn
   and return the completed user/agent turn afterward.
2. The same key exposes any separately authorized Team0 abilities through `/v1/mcp`.

MCP is an ability transport, not the mechanism that makes learning automatic. The hooks own the
before/after lifecycle even when the model never calls a Team0 tool.

The intended user experience is simple: after connection, a user can ask an ordinary question
such as “What are my priorities?” without naming Team0, and can state an update such as “I decided
the next priority is simpler installation.” The first turn should use the authorized Living
Understanding automatically; the second should be contributed automatically and become available
to other connected sessions and the normal Chief after processing.

## Connection model

Every host uses the same two required lifecycle capabilities:

1. `before_turn` loads the relevant governed understanding before the host agent works.
2. `after_turn` returns the completed user/agent exchange for governed learning.

Creating a grant, saving a key, or exposing MCP tools does not prove either capability. Runtime
health remains `setup_incomplete` until both have actually run. This rule applies equally to a
Codex plugin, a Claude Code hook adapter, middleware around a custom agent loop, or any future host.
Optional tool-outcome and proactive-delivery adapters extend the connection without redefining it.

## Configuration

On the first eligible turn, the Codex or Claude adapter opens Team0's secure connection page in the
browser. Sign in and click **Connect Codex** or **Connect Claude Code**. The page returns the
reveal-once key directly to the local adapter; the user does not copy a command, JSON or secret.
Each host receives its own grant, contribution source and secure credential entry. Historical
username-only macOS credentials remain valid for Codex, but are never borrowed by Claude.

The host may still perform its own trust review for the two required lifecycle permissions. Team0
does not label the connection healthy until both permissions have actually run.

Codex starts MCP servers in a separate process before its prompt hooks run. The Codex package
therefore uses the local `scripts/team0_mcp_proxy.py` stdio bridge. The bridge loads only the
Codex-scoped credential from the secure store and forwards MCP requests to Team0 over HTTPS. This
keeps the Codex and Claude Code grants separate while making the MCP tools available on the first
turn after pairing. It negotiates the legacy MCP session version Codex currently sends during
`initialize` (2025-11-25 or 2025-06-18); automatic lifecycle reads then use the modern 2026
projection contract directly. Claude Code continues to use its own HTTP MCP configuration.

Other hosts may inject these values into the agent process from a managed secret/configuration
system:

```bash
TEAM0_API_KEY=the_reveal_once_agent_access_key
TEAM0_CONTRIBUTION_SOURCE_ID=the_source_id_returned_with_the_agent_grant
```

The source ID is not a second credential. It identifies the registered contribution source bound
to the same server-side agent grant. Current runtimes discover it automatically when omitted.

Optional controls:

- `TEAM0_RUNTIME_ENABLED=false` disables the automatic lifecycle.
- `TEAM0_API_BASE_URL` changes the API origin (HTTPS only; loopback HTTP requires an explicit test flag).
- `TEAM0_RUNTIME_READ_TIMEOUT_SECONDS` defaults to `7`; it gives the shared MCP operation a small
  network margin around Team0's fixed five-second server retrieval ceiling.
- `TEAM0_RUNTIME_CONTEXT_MAX_CHARS` defaults to `28000`, including the fixed host policy around
  Team0's server-compiled context.
- `TEAM0_RUNTIME_TOOL_ACTIVITY=false` disables candidate-only tool activity reports.
- `TEAM0_RUNTIME_TRUSTED_ACTION_TOOLS` is a comma-separated allowlist of tool names whose structured
  success receipts may complete an exact Team0 action ID. It defaults to empty.

Each host applies its own trust review before plugin hooks run. No API key is written to logs,
prompts, or source code. macOS uses Keychain; Windows uses current-user protected storage; Linux
uses a private runtime file.

Each host keeps its own credential, data directory and Team0 agent; one host never
borrows another's key. `scripts/host_profile.py` is the single definition of how a
host is detected, where its state lives and which credential it may use.

For local Claude Code development, load the same plugin directory directly:

```bash
claude --plugin-dir ./plugins/team0-agent-runtime
```

The Claude adapter uses Claude's persistent plugin-data directory and correlates lifecycle
callbacks even though `UserPromptSubmit` does not supply a turn ID. It does not fork or reimplement
the Team0 brain. Its brain path has been conformance-tested across fresh Claude Code sessions: an
ordinary preference stated in one session was learned by Team0 and recalled in another.

### OpenClaw

OpenClaw has a native typed-hook adapter in `openclaw/`. It reuses this Python runtime rather than
relying on the model to call a contribution tool. Keep the owner's Team0 MCP connection named
`team0` at `https://api.team0.ai/v1/mcp`; the adapter reads that connection's bearer key locally,
injects governed context at `before_prompt_build`, and submits the exact completed user/assistant
exchange at `agent_end`. It does not put the key into the prompt or log it. It skips internal,
failed, and uncorrelated turns. OpenClaw needs Python 3 in the Gateway environment.

After the public plugin repository is published, run these in the Gateway environment:

```bash
openclaw plugins install team0-agent-runtime --marketplace ybentov1/team0-plugins
openclaw config set plugins.entries.team0-agent-runtime.hooks.allowConversationAccess true --strict-json
openclaw plugins enable team0-agent-runtime
```

Review OpenClaw's install and capability-consent prompts before accepting them. Restart the
Gateway, start a new conversation, and inspect with
`openclaw plugins inspect team0-agent-runtime --runtime --json`: `before_prompt_build` and
`agent_end` must both appear under `typedHooks`. In Docker, run the commands inside the Gateway
container; installing a host-side copy does not activate it inside the container. If an older
workspace `AGENTS.md` contains Team0 standing instructions, remove that block after the native
adapter is working to avoid duplicate model-initiated reads or contributions.

OpenClaw's MCP connection is still used for optional Team0 tools. The native hooks are what make
the read and return automatic. Stop contributions is enforced by the Team0 grant on writes while
reads remain available; Stop all access revokes both. Those owner controls still require an
end-to-end OpenClaw acceptance run before this host is called production-proven.

## Install

Both hosts install from a published catalog, and each connects itself:

```bash
claude plugin marketplace add https://team0.ai/plugins/marketplace.json
claude plugin install team0-agent-runtime@team0

codex plugin marketplace add ybentov1/team0-plugins
codex plugin add team0-agent-runtime@team0
```

Restart the host once. Codex users must also run `/hooks`, review the Team0 plugin
source, and trust its three hooks once; Codex intentionally skips untrusted plugin
hooks. `SessionStart` then asks whether this host is connected. An unconnected host
opens the Team0 connect page immediately and says so in one line, rather than waiting
for a first message to discover it. A connected host prints nothing and runs nothing.

The two published copies are built from this directory: the archive the Team0
frontend build publishes, and the public git marketplace Codex requires.
`scripts/publish_agent_plugin.py` compares both against this directory and, with
`--apply`, pushes the git copy.

`scripts/pair.py --host <id>` pairs from a plain shell, where none of a host's own
markers are present; without it the run would pair as Codex.

## Runtime behavior

- `SessionStart`: checks whether this host holds a credential, and starts pairing if it
  does not. It never reads or writes understanding.
- `UserPromptSubmit`: saves a durable turn identity, calls the same
  `team0_living_understanding` MCP tool used by interactive hosts, and adds its server-owned
  projection as developer context. A timeout degrades open.
- `Stop`: while conversation contribution is enabled, stores the completed turn in a local outbox
  before posting it to `/wm/events`. Delivery is idempotent and replays due outbox records on later
  completed turns, up to eight attempts. If the owner stops conversation contribution, reads remain
  available and completed turns are skipped cleanly without a warning or retry record.

Before each turn, the portable runtime returns `team0.agent_turn_context.v1` with fixed policy and
retrieved data as separate fields. Team0 supplies cross-session direction—decisions, priorities,
preferences, commitments, relationships, and continuity. The host supplies current local evidence
such as code, files, tests, deployments, tool results, and session state. The host may use that
evidence to refine, verify, or execute the Team0 direction, but it must not silently replace it. If
the sources conflict, the host surfaces the conflict; if Team0 already answers the request, it
answers without broad local discovery. Retrieved content remains quoted data, never instructions
or execution authority.

The result also carries the host-neutral `team0.local_evidence.v1` budget. Direction questions use no
host discovery unless current host state could change the concrete next step; in that case the host
may use one narrow, bounded verification batch. Broad discovery requires an explicit user request or
a failed narrow probe. Host-local evidence may confirm that directed work is complete or blocked and
refine the next slice, but cannot silently narrow a cross-agent product direction to the current host
or vendor.

The minimum Codex install therefore asks for two permissions, not seven. Hosts that can expose
structured tool outcomes may additionally call `after_tool`; this is optional and is not part of
the default Codex hook set. Tool activity enters Team0 as attributed candidate evidence. Canonical
action completion additionally requires an exact Team0 action ID and a locally allowlisted trusted
tool.

Accepted conversation records are removed from the local outbox. Failed records remain in the
plugin data directory for inspection/recovery. Hidden reasoning and unbounded intermediate tool
logs are never collected.

The server grounds learning in the exact user message. An explicit owner-direct, first-person
decision may become trusted owner state. The assistant response is retained as attributed context
but never supplies owner authority; an implementation detail invented by the host must not become
owner truth. Promises, requests, reported claims and hypotheticals keep their existing governed
candidate/evidence behavior.

Check local sync health with:

```bash
python3 "$PLUGIN_ROOT/scripts/team0_hook.py" status
```

The same command includes pairing state, hook-file validation, launcher availability, and
credential presence without printing the credential. For a read-only local installation check
that never contacts Team0, run:

```bash
python3 "$PLUGIN_ROOT/scripts/team0_hook.py" self-test
```

Pairing failures are recorded in `~/.team0-agent-runtime/pairing-status.json` (or the configured
runtime data directory). The record contains only the host, lifecycle state, timestamp, and a
coarse error code; it never contains the callback URL or access key. The Codex hook manifest is a
stable security boundary. Runtime behavior belongs in the scripts, so updating the runtime does
not create a new hook-trust prompt unless the manifest itself changes.

For the beta canary, use two fresh sessions after the server change is deployed:

1. In session A, state a new explicit decision without asking the agent to implement it.
2. Wait for the runtime status to show no pending or failed contribution.
3. In session B, ask what you most recently decided about that topic.
4. Ask the normal Team0 Chief the same question.

Both should recall the decision; neither should report implementation details created only by the
external agent. A decision captured before the server correction may remain candidate-only and is
not silently promoted, so the canary must use a new statement.

Installation and discovery are no longer a gap: both hosts install from a published
catalog and connect themselves. Current beta gaps are remote reporting for ongoing
degraded or failed sync after initial activation, pre-turn latency within the shared
five-second target, live three-agent proof with separately paired production
credentials, a full-lifecycle third-host/custom-agent conformance run, and typed
action-outcome proof.

## Structured tool outcome envelope

A custom tool adapter can return this alongside its normal result:

```json
{
  "team0_runtime": {
    "activity": {
      "activity_id": "provider-receipt-123",
      "provider": "linear",
      "tool": "complete_issue",
      "item_type": "issue",
      "item_id": "LIN-42",
      "item_version": "9",
      "change_type": "updated",
      "occurred_at": "2026-09-08T12:00:00Z",
      "title": "Completed LIN-42"
    },
    "action_outcome": {
      "action_item_id": "the-exact-team0-action-id",
      "status": "completed"
    }
  }
}
```

The activity is non-authoritative evidence. The action transition is attempted only when the
current Team0 grant includes `action_items.write` and the emitting tool name appears in
`TEAM0_RUNTIME_TRUSTED_ACTION_TOOLS`.

## Reusing the runtime in another agent

The `runtime/team0_agent_runtime` package has no Codex dependency and uses only Python's standard
library. A host adapter needs to map just three lifecycle boundaries:

```python
from team0_agent_runtime import RuntimeConfig, Team0AgentRuntime

runtime = Team0AgentRuntime(RuntimeConfig.from_environ())

prepared, warning = runtime.prepare_turn(
    session_id=host_session_id,
    turn_id=host_turn_id,
    prompt=user_message,
)
# Put `prepared.policy` in the host's trusted policy channel and `prepared.data`
# in its retrieved-context channel. If the host has only one such channel, use
# `prepared.render()`; it places fixed policy before quoted retrieved data.

warning = runtime.after_turn(
    turn_id=host_turn_id,
    assistant_message=final_agent_message,
)
```

Hosts with different tool-result formats provide a `ToolOutcomeAdapter`; they do not change the
read, conversation, retry, or action-reconciliation logic. A future TypeScript runtime should
implement this same lifecycle and the frozen `team0.agent_runtime.v1` contract rather than copy
Codex hook behavior.

For Python agents, `GenericAgentAdapter` owns those boundaries and works with synchronous or
asynchronous agent loops. The callback receives fixed Team0 policy and retrieved data as separate
fields, so a host with separate system/context channels does not need to parse a prompt:

```python
from team0_agent_runtime import (
    GenericAgentAdapter,
    HostCapabilities,
    RuntimeConfig,
    Team0AgentRuntime,
)

adapter = GenericAgentAdapter(
    Team0AgentRuntime(RuntimeConfig.from_environ()),
    capabilities=HostCapabilities.custom_agent_loop(
        callable_tools=True,
        inbound_events=True,
        callable_agent=True,
        presents_agent_identity=True,
    ),
)

result = await adapter.run_turn_async(
    session_id=host_session_id,
    turn_id=host_turn_id,
    user_message=user_message,
    invoke=lambda turn: my_agent.run(
        user_message=turn.user_message,
        trusted_policy=turn.team0_policy,
        retrieved_context=turn.team0_data,
    ),
)
```

The adapter returns synchronization warnings without replacing the host response. A failed host
turn is never contributed as a completed conversation. Stable host session and turn IDs preserve
cross-session ordering and idempotency.

`adapter.capability_manifest()` returns `team0.connected_agent_capabilities.v1`. It reports host
support and shipped Team0 bridges separately, then exposes their intersection as product support
across Brain, Meetings, Agent Network, and Proactive delivery. A technically capable host therefore
cannot make an unshipped Team0 bridge appear ready. Discovery is still not authorization: it cannot
authorize a tool, source, action, meeting, message, or delivery. The saved server-side agent grant
remains the sole authority source. This lets installers and the Team0 UI present honest states such
as automatic, polling-only, adapter pending, Team0-notetaker bridge, or unavailable without adding
vendor-specific branches.
