"""Runtime configuration sourced from the host environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse


def _enabled(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(value: str | None, default: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except ValueError:
        return default
    return max(1, min(parsed, maximum))


def _positive_float(value: str | None, default: float, maximum: float) -> float:
    try:
        parsed = float(value) if value is not None else default
    except ValueError:
        return default
    return max(0.1, min(parsed, maximum))


@dataclass(frozen=True)
class RuntimeConfig:
    """One host connection to one server-governed Team0 agent grant."""

    api_key: str | None = field(default=None, repr=False)
    contribution_source_id: str | None = None
    api_base_url: str = "https://api.team0.ai/v1"
    data_dir: Path = Path(".team0-agent-runtime")
    host_id: str = "custom-agent"
    enabled: bool = True
    context_timeout_seconds: float = 7.0
    write_timeout_seconds: float = 5.0
    context_max_chars: int = 28_000
    report_tool_activity: bool = True
    trusted_action_tools: frozenset[str] = frozenset()
    surface_errors: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> "RuntimeConfig":
        env = os.environ if environ is None else environ
        data_root = (
            env.get("PLUGIN_DATA")
            or env.get("CLAUDE_PLUGIN_DATA")
            or env.get("TEAM0_RUNTIME_DATA_DIR")
            or str(Path.home() / ".team0-agent-runtime")
        )
        base_url = env.get("TEAM0_API_BASE_URL", "https://api.team0.ai/v1").rstrip("/")
        parsed = urlparse(base_url)
        allow_local_http = _enabled(
            env.get("TEAM0_RUNTIME_ALLOW_INSECURE_LOCAL"), default=False
        ) and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme != "https" and not allow_local_http:
            base_url = "https://api.team0.ai/v1"
        tools = frozenset(
            item.strip()
            for item in env.get("TEAM0_RUNTIME_TRUSTED_ACTION_TOOLS", "").split(",")
            if item.strip()
        )
        return cls(
            api_key=env.get("TEAM0_API_KEY") or env.get("TEAM0_ACCESS_KEY"),
            contribution_source_id=(
                env.get("TEAM0_CONTRIBUTION_SOURCE_ID")
                or env.get("TEAM0_AGENT_SOURCE_ID")
            ),
            api_base_url=base_url,
            data_dir=Path(data_root).expanduser(),
            host_id=(env.get("TEAM0_RUNTIME_HOST_ID") or "custom-agent").strip(),
            enabled=_enabled(env.get("TEAM0_RUNTIME_ENABLED"), default=True),
            context_timeout_seconds=_positive_float(
                env.get("TEAM0_RUNTIME_READ_TIMEOUT_SECONDS"), 7.0, 10.0
            ),
            write_timeout_seconds=_positive_float(
                env.get("TEAM0_RUNTIME_WRITE_TIMEOUT_SECONDS"), 5.0, 30.0
            ),
            context_max_chars=_positive_int(
                env.get("TEAM0_RUNTIME_CONTEXT_MAX_CHARS"), 28_000, 40_000
            ),
            report_tool_activity=_enabled(
                env.get("TEAM0_RUNTIME_TOOL_ACTIVITY"), default=True
            ),
            trusted_action_tools=tools,
            surface_errors=_enabled(env.get("TEAM0_RUNTIME_SURFACE_ERRORS"), default=True),
        )
