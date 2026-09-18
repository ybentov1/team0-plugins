"""Minimal durable turn/outbox storage in the host-provided plugin data directory."""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows host fallback
    fcntl = None


RETRY_DELAYS_SECONDS = (0, 1, 5, 15, 60, 180, 300, 600)
REQUIRED_LIFECYCLE_EVENTS = ("before_turn", "after_turn")
LIFECYCLE_CONTRACT = "team0.agent_runtime.lifecycle.v1"


def stable_id(prefix: str, *parts: object) -> str:
    canonical = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{prefix}_{digest}"


class RuntimeStore:
    def __init__(self, root: Path, *, clock=time.time) -> None:
        self.root = root
        self._clock = clock
        self.turns = root / "turns"
        self.outbox = root / "outbox"
        self.sessions = root / "sessions"
        self.active_turns = root / "active-turns"
        for directory in (
            root,
            self.turns,
            self.outbox,
            self.sessions,
            self.active_turns,
        ):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    def start_turn(
        self, *, session_id: str, turn_id: str, prompt: str, occurred_at: str
    ) -> Mapping[str, Any]:
        with self._locked():
            turn_path = self._item_path(self.turns, turn_id)
            existing = self._read(turn_path)
            if existing is not None:
                return existing
            session_path = self._item_path(self.sessions, session_id)
            session = self._read(session_path) or {"sequence": -1, "previous_turn_id": None}
            sequence = int(session.get("sequence", -1)) + 1
            turn = {
                "session_id": session_id,
                "turn_id": turn_id,
                "previous_turn_id": session.get("previous_turn_id"),
                "sequence": sequence,
                "prompt": prompt,
                "occurred_at": occurred_at,
            }
            self._write(turn_path, turn)
            self._write(session_path, {"sequence": sequence, "previous_turn_id": turn_id})
            return turn

    def get_turn(self, turn_id: str) -> Mapping[str, Any] | None:
        return self._read(self._item_path(self.turns, turn_id))

    def bind_active_turn(self, *, session_id: str, turn_id: str) -> None:
        """Correlate lifecycle callbacks for hosts without a shared turn ID."""

        with self._locked():
            self._write(
                self._item_path(self.active_turns, session_id),
                {"session_id": session_id, "turn_id": turn_id},
            )

    def active_turn_id(self, session_id: str) -> str | None:
        value = self._read(self._item_path(self.active_turns, session_id))
        turn_id = (value or {}).get("turn_id")
        return str(turn_id) if turn_id else None

    def release_active_turn(self, *, session_id: str, turn_id: str) -> None:
        """Release only the mapping owned by this completed callback."""

        path = self._item_path(self.active_turns, session_id)
        with self._locked():
            value = self._read(path)
            if value and value.get("turn_id") == turn_id:
                path.unlink(missing_ok=True)

    def attach_understanding_read(
        self,
        *,
        turn_id: str,
        read_id: str,
        read_status: str,
        context_contract: str,
        context_chars: int,
        latency_ms: float,
    ) -> Mapping[str, Any] | None:
        """Bind content-free retrieval proof to the turn that received it."""

        path = self._item_path(self.turns, turn_id)
        with self._locked():
            turn = self._read(path)
            if turn is None:
                return None
            updated = dict(turn)
            updated["understanding_read"] = {
                "read_id": str(read_id),
                "status": str(read_status),
                "context_contract": str(context_contract),
                "context_chars": max(0, int(context_chars)),
                "latency_ms": max(0.0, float(latency_ms)),
            }
            self._write(path, updated)
            return updated

    def delete_turn(self, turn_id: str) -> None:
        self._item_path(self.turns, turn_id).unlink(missing_ok=True)

    def enqueue(
        self,
        *,
        record_id: str,
        kind: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        path = self._item_path(self.outbox, record_id)
        with self._locked():
            existing = self._read(path)
            if existing is not None:
                return existing
            record = {
                "id": record_id,
                "kind": kind,
                "payload": dict(payload),
                "idempotency_key": idempotency_key,
                "attempts": 0,
                "next_attempt_at": self._clock(),
                "status": "pending",
                "created_at": self._clock(),
            }
            self._write(path, record)
            return record

    def due(self, *, limit: int = 8) -> tuple[Mapping[str, Any], ...]:
        now = self._clock()
        records = []
        for path in sorted(self.outbox.glob("*.json")):
            record = self._read(path)
            if not record or record.get("status") != "pending":
                continue
            if float(record.get("next_attempt_at", 0)) <= now:
                records.append(record)
            if len(records) >= limit:
                break
        return tuple(records)

    def accepted(self, record_id: str) -> None:
        self._item_path(self.outbox, record_id).unlink(missing_ok=True)

    def failed(self, record_id: str, *, error: str, retryable: bool) -> Mapping[str, Any] | None:
        path = self._item_path(self.outbox, record_id)
        with self._locked():
            record = self._read(path)
            if record is None:
                return None
            attempts = int(record.get("attempts", 0)) + 1
            terminal = not retryable or attempts >= len(RETRY_DELAYS_SECONDS)
            updated = dict(record)
            updated.update(
                {
                    "attempts": attempts,
                    "last_error": error[:500],
                    "status": "failed" if terminal else "pending",
                    "next_attempt_at": (
                        None
                        if terminal
                        else self._clock() + RETRY_DELAYS_SECONDS[attempts]
                    ),
                }
            )
            self._write(path, updated)
            return updated

    def counts(self) -> Mapping[str, int]:
        counts = {"pending": 0, "failed": 0}
        for path in self.outbox.glob("*.json"):
            record = self._read(path)
            status = str((record or {}).get("status") or "")
            if status in counts:
                counts[status] += 1
        return counts

    def update_health(self, **values: Any) -> None:
        path = self.root / "health.json"
        with self._locked():
            health = dict(self._read(path) or {})
            if "state" in values:
                values["operational_state"] = values.pop("state")
            health.update(values)
            health["updated_at_epoch"] = self._clock()
            health.update(self.counts())
            self._write(path, health)

    def declare_connection(
        self, *, host_id: str, configured: bool, connection_id: str
    ) -> None:
        path = self.root / "health.json"
        with self._locked():
            health = dict(self._read(path) or {})
            identity_changed = (
                health.get("connection_id") != connection_id
                or health.get("lifecycle_contract") != LIFECYCLE_CONTRACT
            )
            if identity_changed:
                for event in REQUIRED_LIFECYCLE_EVENTS:
                    health.pop(f"{event}_seen_at", None)
                health.pop("operational_state", None)
            health.update(
                {
                    "connection_id": connection_id,
                    "host_id": host_id,
                    "configured": configured,
                    "lifecycle_contract": LIFECYCLE_CONTRACT,
                    "required_lifecycle_events": list(REQUIRED_LIFECYCLE_EVENTS),
                    "updated_at_epoch": self._clock(),
                    **self.counts(),
                }
            )
            self._write(path, health)

    def mark_lifecycle_event(self, event: str) -> None:
        if event not in REQUIRED_LIFECYCLE_EVENTS:
            raise ValueError(f"Unsupported required lifecycle event: {event}")
        self.update_health(**{f"{event}_seen_at": _utc_now()})

    def health(self) -> Mapping[str, Any]:
        health = dict(self._read(self.root / "health.json") or {})
        health.update(self.counts())
        required = tuple(health.get("required_lifecycle_events") or REQUIRED_LIFECYCLE_EVENTS)
        observed = tuple(event for event in required if health.get(f"{event}_seen_at"))
        health["observed_lifecycle_events"] = list(observed)
        health["missing_lifecycle_events"] = [
            event for event in required if event not in observed
        ]
        health["state"] = self._connection_state(health)
        health["ready"] = health["state"] == "healthy"
        health["capabilities"] = {
            "understanding": {
                "required": "before_turn" in required,
                "verified": "before_turn" in observed,
            },
            "learning": {
                "required": "after_turn" in required,
                "verified": "after_turn" in observed,
            },
        }
        return health

    @staticmethod
    def _connection_state(health: Mapping[str, Any]) -> str:
        if not health.get("configured"):
            return "not_connected"
        if int(health.get("failed") or 0) > 0:
            return "failed"
        if int(health.get("pending") or 0) > 0:
            return "delayed"
        operational = str(health.get("operational_state") or "")
        if operational in {"revoked", "failed", "degraded", "delayed"}:
            return operational
        if health.get("missing_lifecycle_events"):
            return "setup_incomplete"
        return "healthy"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        lock_path = self.root / ".lock"
        with lock_path.open("a+") as handle:
            os.chmod(lock_path, 0o600)
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _item_path(directory: Path, item_id: str) -> Path:
        digest = hashlib.sha256(item_id.encode("utf-8")).hexdigest()
        return directory / f"{digest}.json"

    @staticmethod
    def _read(path: Path) -> Mapping[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return value if isinstance(value, Mapping) else None

    @staticmethod
    def _write(path: Path, value: Mapping[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
