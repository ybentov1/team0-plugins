#!/usr/bin/env python3
"""Hook-host lifecycle adapter for the host-neutral Team0 runtime."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping


PLUGIN_ROOT = Path(os.environ.get("PLUGIN_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(PLUGIN_ROOT / "runtime"))

from team0_agent_runtime import RuntimeConfig, Team0AgentRuntime  # noqa: E402
from team0_agent_runtime.storage import stable_id  # noqa: E402
from credential_store import load_credential  # noqa: E402
from host_profile import (  # noqa: E402
    configure_environment,
    data_root,
    detect_host_id,
    host_credential,
)


def _host_credential(host_id: str, root=None):
    """One host rule, reading through this module's patchable accessor."""

    return host_credential(host_id, root=root, loader=lambda root=None: load_credential())
from pairing_status import read_status, status_path, write_status  # noqa: E402


def _host_id() -> str:
    return detect_host_id()


def _configure_host_environment(host_id: str) -> None:
    configure_environment(host_id)


def _load_saved_credential(host_id: str) -> str | None:
    ambient_key = os.environ.get("TEAM0_API_KEY") or os.environ.get("TEAM0_ACCESS_KEY")
    saved = _host_credential(host_id)
    # The unscoped legacy entry belongs to Codex, but an explicit ambient key
    # still wins over it, as it did before host profiles were shared.
    if saved and not str(saved.get("host_id") or "").strip() and ambient_key:
        saved = None
    if saved:
        key = str(saved["key"])
        # A child agent can inherit its parent's environment. A credential saved
        # for this host is authoritative and must replace that ambient identity.
        os.environ["TEAM0_API_KEY"] = key
        if saved.get("contribution_source_id"):
            os.environ["TEAM0_CONTRIBUTION_SOURCE_ID"] = str(
                saved["contribution_source_id"]
            )
        return key
    return ambient_key


def _start_pairing(host_id: str) -> bool:
    platform_options = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    try:
        subprocess.Popen(
            [sys.executable, str(PLUGIN_ROOT / "scripts" / "pair.py")],
            env={
                **os.environ,
                "PLUGIN_ROOT": str(PLUGIN_ROOT),
                "TEAM0_RUNTIME_HOST_ID": host_id,
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **platform_options,
        )
    except (OSError, subprocess.SubprocessError):
        write_status(
            "failed",
            host_id=host_id,
            error_code="pair_process_start",
        )
        return False
    return True


def _launcher_name() -> str:
    if sys.platform == "darwin":
        return "open" if shutil.which("open") else "unavailable"
    if os.name == "nt":
        return "os.startfile"
    return "xdg-open" if shutil.which("xdg-open") else "unavailable"


def _hook_manifest_diagnostics() -> Mapping[str, Any]:
    path = PLUGIN_ROOT / "hooks" / "hooks.json"
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
    }
    if not path.is_file():
        result["valid"] = False
        return result
    try:
        raw = path.read_bytes()
        parsed = json.loads(raw.decode("utf-8"))
        result.update(
            {
                "valid": isinstance(parsed, Mapping) and isinstance(parsed.get("hooks"), Mapping),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        result["valid"] = False
    return result


def _diagnostics(host_id: str) -> Mapping[str, Any]:
    root = data_root(host_id)
    saved = _host_credential(host_id, root=root)
    lock = root / "pairing.lock"
    lock_age = None
    try:
        lock_age = max(0.0, time.time() - lock.stat().st_mtime)
    except OSError:
        pass
    return {
        "host_id": host_id,
        "plugin_root": str(PLUGIN_ROOT),
        "hook_manifest": _hook_manifest_diagnostics(),
        "hook_scripts": {
            "before_turn": (PLUGIN_ROOT / "scripts" / "team0_hook.py").is_file(),
            "after_turn": (PLUGIN_ROOT / "scripts" / "team0_hook.py").is_file(),
            "pairing": (PLUGIN_ROOT / "scripts" / "pair.py").is_file(),
        },
        "launcher": _launcher_name(),
        "credential_present": bool(saved and str(saved.get("host_id") or host_id) == host_id),
        "pairing": {
            "status_file": str(status_path(root)),
            "status": read_status(root),
            "lock_active": lock_age is not None and lock_age <= 300,
            "lock_age_seconds": lock_age,
        },
    }


def _self_test(host_id: str) -> int:
    diagnostics = _diagnostics(host_id)
    manifest = diagnostics["hook_manifest"]
    scripts = diagnostics["hook_scripts"]
    checks_passed = bool(
        manifest.get("exists")
        and manifest.get("valid")
        and all(scripts.values())
        and diagnostics["launcher"] != "unavailable"
    )
    print(
        json.dumps(
            {
                "checks_passed": checks_passed,
                "connection_ready": bool(diagnostics["credential_present"]),
                "next_action": (
                    "start a new conversation; Team0 will run automatically"
                    if diagnostics["credential_present"]
                    else "pair this host once from Team0"
                ),
                **diagnostics,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if checks_passed else 1


def _input() -> Mapping[str, Any]:
    try:
        value = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _output(*, event_name: str | None = None, context: str | None = None, warning: str | None = None) -> None:
    body: dict[str, Any] = {}
    if warning:
        body["systemMessage"] = warning
    if event_name and context:
        body["hookSpecificOutput"] = {
            "hookEventName": event_name,
            "additionalContext": context,
        }
    print(json.dumps(body, ensure_ascii=False))


def _turn_id(event: Mapping[str, Any], *, host_id: str) -> str:
    supplied = str(event.get("turn_id") or "").strip()
    if supplied:
        return supplied
    transcript_path = str(event.get("transcript_path") or "")
    try:
        transcript_revision = Path(transcript_path).stat().st_size
    except OSError:
        transcript_revision = 0
    return stable_id(
        "hostturn",
        host_id,
        event.get("session_id") or "unknown-session",
        transcript_path,
        transcript_revision,
        event.get("prompt") or "",
    )


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    host_id = _host_id()
    _configure_host_environment(host_id)
    if command == "self-test":
        return _self_test(host_id)
    if command == "before-turn" and not _load_saved_credential(host_id):
        launched = _start_pairing(host_id)
        _output(
            warning=(
                "Team0 connection is required. Pairing was started in your browser; "
                "your agent will continue without Team0 for this turn."
                if launched
                else "Team0 is not connected and automatic pairing could not start; "
                "your agent will continue without Team0 for this turn."
            )
        )
        return 0
    _load_saved_credential(host_id)
    config = RuntimeConfig.from_environ()
    if command == "status":
        try:
            health = Team0AgentRuntime(config).store.health()
        except Exception as error:  # diagnostics must remain usable when storage is damaged
            health = {
                "state": "unavailable",
                "ready": False,
                "error_code": type(error).__name__,
            }
        print(json.dumps({
            "health": health,
            "diagnostics": _diagnostics(host_id),
        }, indent=2, sort_keys=True))
        return 0
    runtime = Team0AgentRuntime(config)
    event = _input() if command != "status" else {}
    try:
        if command == "before-turn":
            session_id = str(event.get("session_id") or "unknown-session")
            turn_id = _turn_id(event, host_id=host_id)
            context, warning = runtime.before_turn(
                session_id=session_id,
                turn_id=turn_id,
                prompt=str(event.get("prompt") or ""),
            )
            if runtime.store.get_turn(turn_id):
                runtime.store.bind_active_turn(session_id=session_id, turn_id=turn_id)
            _output(event_name="UserPromptSubmit", context=context, warning=warning)
        elif command == "after-turn":
            session_id = str(event.get("session_id") or "unknown-session")
            turn_id = str(event.get("turn_id") or "").strip()
            turn_id = turn_id or runtime.store.active_turn_id(session_id) or ""
            warning = None
            if turn_id:
                warning = runtime.after_turn(
                    turn_id=turn_id,
                    assistant_message=(
                        str(event["last_assistant_message"])
                        if event.get("last_assistant_message") is not None
                        else None
                    ),
                )
                runtime.store.release_active_turn(
                    session_id=session_id,
                    turn_id=turn_id,
                )
            _output(warning=warning)
        elif command == "after-tool":
            _output(warning=runtime.after_tool(event))
        elif command == "flush":
            _output(warning=runtime.flush_pending().get("message"))
        else:
            _output(warning="Unknown Team0 runtime command.")
            return 2
    except Exception as error:  # fail open; the host must keep working
        runtime.store.update_health(state="failed", last_runtime_error=type(error).__name__)
        _output(
            warning=(
                "Team0 sync encountered an internal error; your agent will continue "
                "without blocking."
                if config.surface_errors
                else None
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
