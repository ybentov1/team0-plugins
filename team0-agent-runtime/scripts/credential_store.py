"""Platform credential storage for Team0 agent-runtime adapters."""

from __future__ import annotations

import ctypes
import getpass
import hashlib
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any, Mapping


KEYCHAIN_SERVICE = "ai.team0.agent-runtime"
_SECRET_FILE = "credential.bin"
_METADATA_FILE = "connection.json"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


def data_dir(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    return Path(
        env.get("PLUGIN_DATA")
        or env.get("TEAM0_RUNTIME_DATA_DIR")
        or Path.home() / ".team0-agent-runtime"
    ).expanduser()


def _write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _macos_account(target: Path, host_id: str) -> str:
    root_digest = hashlib.sha256(str(target.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"{getpass.getuser()}:{host_id}:{root_digest}"


def _macos_store(key: str, account: str) -> None:
    subprocess.run(
        [
            "security", "add-generic-password", "-U", "-a", account,
            "-s", KEYCHAIN_SERVICE, "-w", key,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _macos_load(account: str) -> str | None:
    result = subprocess.run(
        [
            "security", "find-generic-password", "-a", account,
            "-s", KEYCHAIN_SERVICE, "-w",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or None


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_ulong),
        ("data", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _windows_transform(payload: bytes, *, protect: bool) -> bytes:
    buffer = ctypes.create_string_buffer(payload)
    input_blob = _DataBlob(
        len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    )
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    success = function(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    )
    if not success:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.data, output_blob.size)
    finally:
        kernel32 = ctypes.windll.kernel32
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(ctypes.cast(output_blob.data, ctypes.c_void_p))


def _windows_protect(payload: bytes) -> bytes:
    return _windows_transform(payload, protect=True)


def _windows_unprotect(payload: bytes) -> bytes:
    return _windows_transform(payload, protect=False)


def store_credential(
    key: str,
    source_id: str,
    host_id: str,
    *,
    root: Path | None = None,
) -> None:
    target = root or data_dir()
    system = platform.system()
    if system == "Darwin":
        account = _macos_account(target, host_id)
        try:
            _macos_store(key, account)
        except (OSError, subprocess.SubprocessError):
            # A Codex pairing callback is a background process. macOS may refuse the
            # interactive Keychain authorization even though the browser callback succeeded.
            # Match the Linux CLI contract instead of losing the only copy of the grant.
            _write_private(target / _SECRET_FILE, key.encode("utf-8"))
        else:
            # A successful `security add-generic-password` is not sufficient if the newly
            # created ACL cannot be read by the later hook/MCP child. Verify the round trip.
            if _macos_load(account) != key:
                _write_private(target / _SECRET_FILE, key.encode("utf-8"))
    elif system == "Windows":
        _write_private(target / _SECRET_FILE, _windows_protect(key.encode("utf-8")))
    else:
        _write_private(target / _SECRET_FILE, key.encode("utf-8"))
    metadata = json.dumps(
        {"contribution_source_id": source_id, "host_id": host_id},
        separators=(",", ":"),
    ).encode("utf-8")
    _write_private(target / _METADATA_FILE, metadata)


def load_credential(*, root: Path | None = None) -> Mapping[str, Any] | None:
    target = root or data_dir()
    system = platform.system()
    try:
        try:
            metadata = json.loads((target / _METADATA_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            metadata = {}
        if system == "Darwin":
            host_id = metadata.get("host_id")
            key = (
                _macos_load(_macos_account(target, str(host_id)))
                if host_id
                else None
            )
            # The legacy username-only entry predates every non-Codex adapter.
            # Preserve it for Codex (and metadata-free legacy installs), but never
            # let a newly identified host borrow that unscoped identity.
            if not key and (not host_id or host_id == "codex"):
                key = _macos_load(getpass.getuser())
            if not key:
                try:
                    key = (target / _SECRET_FILE).read_text(encoding="utf-8").strip()
                except OSError:
                    key = None
        elif system == "Windows":
            key = _windows_unprotect((target / _SECRET_FILE).read_bytes()).decode("utf-8")
        else:
            key = (target / _SECRET_FILE).read_text(encoding="utf-8").strip()
        if not key:
            return None
        return {
            "key": key,
            "contribution_source_id": metadata.get("contribution_source_id"),
            "host_id": metadata.get("host_id"),
        }
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None
