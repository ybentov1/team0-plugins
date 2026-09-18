#!/usr/bin/env python3
"""Codex setup adapter for the host-neutral Team0 Agent Runtime."""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "runtime"))

from team0_agent_runtime import ApiError, Team0ApiClient  # noqa: E402
from credential_store import load_credential, store_credential  # noqa: E402


def main() -> int:
    host_id = (os.environ.get("TEAM0_RUNTIME_HOST_ID") or "codex").strip()
    saved = load_credential()
    existing = str(saved["key"]) if saved else None
    prompt = "Team0 access key"
    if existing:
        prompt += " (leave blank to reuse the saved key)"
    key = getpass.getpass(prompt + ": ").strip() or existing
    if not key:
        print("No Team0 access key supplied.", file=sys.stderr)
        return 2
    client = Team0ApiClient(
        base_url="https://api.team0.ai/v1",
        api_key=key,
    )
    try:
        binding = client.get_agent_runtime_binding()
    except ApiError as error:
        print(f"Team0 rejected this setup: {error.detail}", file=sys.stderr)
        return 1
    source_id = str(binding["contribution_source_id"])
    store_credential(key, source_id, host_id)
    print(f"Team0 access is saved for {host_id}.")
    print(f"Start a new conversation in {host_id}.")
    print(f"{host_id} will ask once to let Team0 load your understanding and learn from completed work.")
    print("The connection is complete only after both permissions have run successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
