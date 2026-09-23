#!/usr/bin/env python3
"""One definition of every host this plugin can run inside.

The lifecycle hook, the pairing flow and the MCP bridge all need the same three
answers: which host am I, where does this host keep its Team0 state, and which
stored credential may this host use. Each of those used to answer separately,
which is how Codex ended up with Team0 abilities while Claude Code had none.
Adding a host means adding one entry here, not editing three scripts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

import credential_store

DEFAULT_ROOT_NAME = ".team0-agent-runtime"


def default_root() -> Path:
    # Resolved per call: the home directory is a test seam and can change.
    return Path.home() / DEFAULT_ROOT_NAME


@dataclass(frozen=True)
class HostProfile:
    """How one agent host is detected, stored and credentialed."""

    id: str
    name: str
    # Environment marker the host sets for its own plugin processes.
    marker: str
    # The host's own plugin-data variable, when it provides one.
    data_dir_env: Optional[str] = None
    # Codex predates host-scoped credentials and still owns the unscoped entry.
    accepts_unscoped_credential: bool = False
    # Codex pairs into the stable user root so its separately started MCP
    # bridge and its hooks share one location across plugin reinstalls.
    uses_shared_root: bool = False
    # Where this host keeps per-install plugin data, for processes it starts
    # without passing that directory on (its MCP servers, for example).
    data_dir_glob: Optional[str] = None


HOSTS: tuple[HostProfile, ...] = (
    HostProfile(
        id="codex",
        name="Codex",
        marker="PLUGIN_ROOT",
        data_dir_env="TEAM0_RUNTIME_DATA_DIR",
        accepts_unscoped_credential=True,
        uses_shared_root=True,
    ),
    HostProfile(
        id="claude-code",
        name="Claude Code",
        marker="CLAUDE_PLUGIN_ROOT",
        data_dir_env="CLAUDE_PLUGIN_DATA",
        data_dir_glob="~/.claude/plugins/data/*",
    ),
    HostProfile(
        id="openclaw",
        name="OpenClaw",
        marker="OPENCLAW_PLUGIN_ROOT",
        data_dir_env="TEAM0_RUNTIME_DATA_DIR",
    ),
)
_BY_ID = {host.id: host for host in HOSTS}
DEFAULT_HOST = _BY_ID["codex"]


def profile(host_id: str | None) -> HostProfile:
    return _BY_ID.get(str(host_id or "").strip(), DEFAULT_HOST)


def detect_host_id(environ: Mapping[str, str] | None = None) -> str:
    """Name the host from its own markers, explicit setting first.

    A host can leak another host's marker into child processes, so the first
    host whose marker is present wins in declaration order and Codex, whose
    marker is the generic one, is checked first.
    """

    env = os.environ if environ is None else environ
    explicit = str(env.get("TEAM0_RUNTIME_HOST_ID") or "").strip()
    if explicit:
        return explicit
    for host in HOSTS:
        if env.get(host.marker):
            return host.id
    return DEFAULT_HOST.id


def data_root(host_id: str, environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    host = profile(host_id)
    explicit = env.get("TEAM0_RUNTIME_DATA_DIR") if host.uses_shared_root else None
    if explicit:
        return Path(explicit).expanduser()
    if host.uses_shared_root:
        return default_root()
    provided = env.get(host.data_dir_env) if host.data_dir_env else None
    if provided:
        return Path(provided).expanduser()
    return default_root() / host.id


def current_root(host_id: str, environ: Mapping[str, str] | None = None) -> Path:
    """The root an already-configured Team0 process should use.

    ``configure_environment`` decides the canonical location and publishes it;
    child processes (pairing, the MCP bridge) read that decision back instead of
    deciding again, so one host never splits its state across two directories.
    """

    env = os.environ if environ is None else environ
    for name in ("PLUGIN_DATA", "TEAM0_RUNTIME_DATA_DIR"):
        value = env.get(name)
        if value:
            return Path(value).expanduser()
    return data_root(host_id, env)


def candidate_roots(host_id: str, environ: Mapping[str, str] | None = None) -> list[Path]:
    """Every place this host's credential could live, best first.

    A host may start its MCP server without telling it which plugin-data
    directory the lifecycle hook used, so the bridge looks in the host's own
    data directories rather than reporting the agent as unconnected.
    """

    env = os.environ if environ is None else environ
    roots = [current_root(host_id, env), data_root(host_id, env), default_root() / host_id]
    glob_pattern = profile(host_id).data_dir_glob
    if glob_pattern:
        expanded = Path(glob_pattern).expanduser()
        try:
            roots.extend(sorted(Path(expanded.anchor).glob(str(expanded.relative_to(expanded.anchor)))))
        except (OSError, ValueError):
            pass
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def configure_environment(host_id: str, environ: Mapping[str, str] | None = None) -> Path:
    """Point every Team0 process in this host at the host's own state."""

    env = os.environ if environ is None else environ
    root = data_root(host_id, env)
    env.setdefault("TEAM0_RUNTIME_HOST_ID", host_id)  # type: ignore[union-attr]
    env["PLUGIN_DATA"] = str(root)  # type: ignore[index]
    if profile(host_id).uses_shared_root:
        env["TEAM0_RUNTIME_DATA_DIR"] = str(root)  # type: ignore[index]
    return root


def host_credential(
    host_id: str,
    root: Path | None = None,
    loader: Callable[..., Optional[Mapping[str, object]]] | None = None,
) -> Optional[Mapping[str, object]]:
    """The credential this host is allowed to use, and never another host's.

    A key saved for one host is that host's identity in Team0; borrowing it
    would attribute one agent's reads and contributions to another. Callers pass
    their own ``loader`` so each entry point keeps one seam for tests.
    """

    host = profile(host_id)
    read = loader or credential_store.load_credential
    for candidate in ([root] if root else candidate_roots(host_id)):
        saved = read(root=candidate)
        if not saved:
            continue
        saved_host = str(saved.get("host_id") or "").strip()
        if saved_host == host.id:
            return saved
        if not saved_host and host.accepts_unscoped_credential:
            return saved
    return None
