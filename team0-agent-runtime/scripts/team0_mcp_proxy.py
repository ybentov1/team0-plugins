#!/usr/bin/env python3
"""Bridge a host's stdio MCP traffic to Team0's authenticated HTTP endpoint.

Hosts start MCP servers as separate processes, before the prompt hooks run, so
a hook cannot populate ``TEAM0_API_KEY`` in the environment of a remote HTTP
MCP server.  This process is the MCP server from the host's perspective and
loads that host's key from the same secure store as the lifecycle hook before
forwarding each JSON-RPC message.  Every host uses it, so Team0's abilities do
not depend on the user having configured an MCP server by hand.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SCRIPT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_ROOT))

from credential_store import load_credential  # noqa: E402
from host_profile import detect_host_id, host_credential, profile  # noqa: E402


DEFAULT_BASE_URL = "https://api.team0.ai/v1"
# The Team0 endpoint exposes the modern 2026 adapter for direct runtime calls,
# but its Streamable HTTP MCP handshake currently accepts the legacy versions
# that Codex sends.  Keep the bridge on the negotiated MCP session version.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18")
PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]


def _api_key(host_id: str | None = None) -> str | None:
    ambient = os.environ.get("TEAM0_API_KEY") or os.environ.get("TEAM0_ACCESS_KEY")
    if ambient:
        return ambient
    # One host never borrows another host's grant: the key is that host's
    # identity in Team0, and its reads and contributions are attributed to it.
    saved = host_credential(
        host_id or detect_host_id(), loader=lambda root=None: load_credential(root=root)
    )
    if not saved:
        return None
    return str(saved.get("key") or "").strip() or None


def _endpoint() -> str:
    base_url = os.environ.get("TEAM0_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    return f"{base_url}/mcp"


def _sse_payloads(raw: bytes) -> list[bytes]:
    payloads: list[bytes] = []
    data_lines: list[str] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif not line and data_lines:
            payloads.append("\n".join(data_lines).encode("utf-8"))
            data_lines = []
    if data_lines:
        payloads.append("\n".join(data_lines).encode("utf-8"))
    return [payload for payload in payloads if payload and payload != b"[DONE]"]


class McpHttpBridge:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._session_id: str | None = None
        self._protocol_version: str | None = None

    def _version_for(self, message: Mapping[str, Any]) -> str:
        """Use the version selected by initialize for every MCP request."""

        if message.get("method") == "initialize":
            params = message.get("params")
            requested = params.get("protocolVersion") if isinstance(params, Mapping) else None
            if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
                self._protocol_version = requested
        return self._protocol_version or PROTOCOL_VERSION

    def forward(self, message: Mapping[str, Any]) -> list[bytes]:
        body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers = {
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": self._version_for(message),
            "User-Agent": "team0-agent-runtime/0.1.0",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        request = Request(_endpoint(), data=body, method="POST", headers=headers)
        try:
            with urlopen(request, timeout=90) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self._session_id = session_id
                raw = response.read()
                content_type = response.headers.get("Content-Type", "").lower()
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"Team0 MCP HTTP {error.code}: {detail}") from None
        except (OSError, URLError, TimeoutError) as error:
            raise RuntimeError(f"Team0 MCP unavailable: {error}") from None
        if not raw:
            return []
        if "text/event-stream" in content_type:
            return _sse_payloads(raw)
        return [raw]


def _error_response(message: Mapping[str, Any], detail: str) -> bytes:
    response: dict[str, Any] = {
        "jsonrpc": "2.0",
        "error": {"code": -32000, "message": detail},
    }
    if "id" in message:
        response["id"] = message["id"]
    return json.dumps(response, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def main() -> int:
    host_id = detect_host_id()
    api_key = _api_key(host_id)
    if not api_key:
        print(
            f"Team0 is not connected for {profile(host_id).name}; "
            "Team0 will connect it on the next turn, or connect it from Team0.",
            file=sys.stderr,
        )
        return 1
    bridge = McpHttpBridge(api_key)
    for line in sys.stdin:
        if not line.strip():
            continue
        message: Mapping[str, Any] = {}
        try:
            message = json.loads(line)
            if not isinstance(message, Mapping):
                raise ValueError("MCP message must be an object")
            payloads = bridge.forward(message)
        except (json.JSONDecodeError, ValueError, RuntimeError) as error:
            payloads = [_error_response(message, str(error))]
        for payload in payloads:
            sys.stdout.buffer.write(payload.rstrip(b"\n") + b"\n")
            sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
