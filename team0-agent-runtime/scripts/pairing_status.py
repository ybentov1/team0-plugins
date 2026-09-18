#!/usr/bin/env python3
"""Small, secret-free status record for the local Team0 pairing flow."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from credential_store import data_dir


STATUS_FILENAME = "pairing-status.json"
STATUS_SCHEMA = "team0.agent_runtime.pairing_status.v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def status_path(root: Path | None = None) -> Path:
    return (root or data_dir()) / STATUS_FILENAME


def write_status(
    state: str,
    *,
    host_id: str,
    root: Path | None = None,
    browser_opened: bool | None = None,
    error_code: str | None = None,
) -> Mapping[str, Any]:
    """Write only operational metadata; never persist the callback URL or key."""

    payload: dict[str, Any] = {
        "schema": STATUS_SCHEMA,
        "state": state,
        "host_id": host_id,
        "updated_at": _now(),
    }
    if browser_opened is not None:
        payload["browser_opened"] = bool(browser_opened)
    if error_code:
        payload["error_code"] = error_code

    target = status_path(root)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent, text=True
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return payload


def read_status(root: Path | None = None) -> Mapping[str, Any] | None:
    try:
        value = json.loads(status_path(root).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None
