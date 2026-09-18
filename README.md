# Team0 plugins

The Team0 agent runtime: your Team0 Living Understanding is read before every
turn in your coding agent, and every finished conversation is returned to Team0
so it keeps learning. You need a [Team0](https://team0.ai) account.

## Install

**Claude Code**

    claude plugin marketplace add ybentov1/team0-plugins
    claude plugin install team0-agent-runtime@team0

**Codex**

    codex plugin marketplace add ybentov1/team0-plugins
    codex plugin add team0-agent-runtime@team0

Then start a new session. Team0 opens a page in your browser once; click
**Connect**, and every session after that is automatic.

## What it does on your machine

- Before each turn it asks Team0 for the part of your understanding that is
  relevant to what you just asked, and adds it as context.
- After each turn it returns the finished conversation to Team0. Contributions
  are recorded as candidates and attributed to this agent.
- On the first turn, if this host is not connected yet, it opens the Team0
  connect page and saves the key it returns in your system credential store
  (Keychain on macOS). The key is never shown, copied or logged.

Each host gets its own Team0 agent, its own key and its own local state, so
Claude Code and Codex are never mistaken for one another.

You can stop an agent's access or its contributions at any time in Team0, under
Living Understanding, then Agents.

## Requirements

Python 3.9 or newer on your machine, and a Team0 account.

## Support

support@team0.ai
