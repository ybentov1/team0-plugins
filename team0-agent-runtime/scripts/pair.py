#!/usr/bin/env python3
"""Pair a local agent host with Team0 without exposing a long-lived key."""

from __future__ import annotations

import hmac
import html
import os
import secrets
import subprocess
import sys
import time
import webbrowser
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qs, urlencode


PLUGIN_ROOT = Path(os.environ.get("PLUGIN_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(PLUGIN_ROOT / "runtime"))

from team0_agent_runtime import ApiError, Team0ApiClient  # noqa: E402
from credential_store import load_credential, store_credential  # noqa: E402
from host_profile import current_root, detect_host_id  # noqa: E402
from pairing_status import write_status  # noqa: E402


PAIRING_TIMEOUT_SECONDS = 300


def _open_connect_url(connect_url: str) -> bool:
    """Open the pairing page even when Python's browser registry is stale.

    Codex starts this process detached from the hook. On macOS, ``webbrowser``
    can return false (or target a stale browser registration) without raising,
    which previously left pairing running with no visible page. Use the native
    launcher as a second attempt and return whether either launch was accepted.
    """
    try:
        if webbrowser.open(connect_url):
            return True
    except (OSError, webbrowser.Error):
        pass

    if sys.platform == "darwin":
        try:
            subprocess.Popen(
                ["open", connect_url],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    if os.name == "nt":
        try:
            os.startfile(connect_url)  # type: ignore[attr-defined]
            return True
        except (AttributeError, OSError):
            return False

    # Linux desktop environments conventionally expose the default browser
    # through xdg-open. Keep this as a native fallback for hosts where Python's
    # browser registry is incomplete (or the hook runs outside an interactive
    # shell).
    try:
        subprocess.Popen(
            ["xdg-open", connect_url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _holder_is_running(path: Path) -> bool:
    """Whether the process that wrote this lock still exists."""

    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextmanager
def _pairing_lock(path: Path) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A pairing that was abandoned (browser closed, machine slept, process
    # killed) must never block every later attempt: reclaim the lock as soon as
    # its holder is gone, rather than waiting out the full pairing timeout.
    try:
        expired = time.time() - path.stat().st_mtime > PAIRING_TIMEOUT_SECONDS
        if expired or not _holder_is_running(path):
            path.unlink()
    except FileNotFoundError:
        pass
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        yield False
        return
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        yield True
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _page(title: str, message: str) -> bytes:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;"
        "background:#f7f1e7;color:#25221f;display:grid;place-items:center;"
        "min-height:100vh;margin:0}.card{max-width:34rem;padding:2.5rem;"
        "border:1px solid #ded5c8;border-radius:18px;background:#fffaf2;"
        "box-shadow:0 18px 50px rgba(44,37,30,.08)}h1{font-size:1.6rem;"
        "margin:0 0 .75rem}p{line-height:1.6;color:#70665b;margin:0}</style>"
        f"</head><body><main class='card'><h1>{html.escape(title)}</h1>"
        f"<p>{html.escape(message)}</p></main></body></html>"
    ).encode("utf-8")


def _handler(
    state: str,
    host_id: str,
    outcome: dict[str, str | bool],
    credential_root: Path | None = None,
):
    class PairingHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/complete":
                self.send_error(404)
                return
            try:
                length = min(int(self.headers.get("Content-Length", "0")), 16_384)
                values = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
                returned_state = values.get("state", [""])[0]
                key = values.get("credential", [""])[0]
                if not hmac.compare_digest(returned_state, state) or not key.startswith("t0_"):
                    raise ValueError("invalid pairing response")
                client = Team0ApiClient(base_url="https://api.team0.ai/v1", api_key=key)
                binding = client.get_agent_runtime_binding()
                store_credential(
                    key,
                    str(binding["contribution_source_id"]),
                    host_id,
                    root=credential_root,
                )
                saved = load_credential(root=credential_root)
                if not saved or saved.get("key") != key or saved.get("host_id") != host_id:
                    raise OSError("credential did not survive secure-store round trip")
                outcome["connected"] = True
                write_status("connected", host_id=host_id, root=credential_root)
                body = _page("Team0 is connected", "Return to your agent and start a new conversation.")
                status = 200
            except (ApiError, KeyError, OSError, subprocess.SubprocessError, ValueError):
                outcome["failed"] = True
                write_status("failed", host_id=host_id, root=credential_root, error_code="callback_rejected")
                body = _page("Connection failed", "Return to Team0 and try connecting again.")
                status = 400
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return PairingHandler


def main() -> int:
    host_id = detect_host_id()
    credential_root = current_root(host_id)
    with _pairing_lock(credential_root / "pairing.lock") as acquired:
        if not acquired:
            return 0
        server = None
        browser_opened = None
        try:
            state = secrets.token_urlsafe(32)
            outcome: dict[str, str | bool] = {}
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _handler(state, host_id, outcome, credential_root),
            )
            server.timeout = 1
            callback = f"http://127.0.0.1:{server.server_port}/complete"
            connect_base = os.environ.get(
                "TEAM0_RUNTIME_CONNECT_URL", "https://team0.ai/connect/agent-runtime"
            )
            connect_url = f"{connect_base}?{urlencode({'host': host_id, 'callback': callback, 'state': state})}"
            browser_opened = _open_connect_url(connect_url)
            write_status(
                "waiting",
                host_id=host_id,
                root=credential_root,
                browser_opened=browser_opened,
            )
            deadline = time.monotonic() + PAIRING_TIMEOUT_SECONDS
            while time.monotonic() < deadline and not outcome:
                server.handle_request()
            if not outcome:
                write_status(
                    "expired",
                    host_id=host_id,
                    root=credential_root,
                    browser_opened=browser_opened,
                    error_code="timeout",
                )
            return 0 if outcome.get("connected") else 1
        except (OSError, RuntimeError, ValueError):
            write_status("failed", host_id=host_id, root=credential_root, error_code="local_startup")
            return 1
        finally:
            if server is not None:
                server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
