"""Host-extensible normalization for bounded external tool outcomes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class ToolOutcome:
    activity: Mapping[str, Any] | None = None
    completed_action_id: str | None = None


class ToolOutcomeAdapter(Protocol):
    """Turn a host-specific tool event into Team0's neutral receipt shape."""

    def normalize(self, event: Mapping[str, Any]) -> ToolOutcome | None:
        ...


class EnvelopeToolOutcomeAdapter:
    """Read an explicit `team0_runtime` envelope without guessing success."""

    def normalize(self, event: Mapping[str, Any]) -> ToolOutcome | None:
        metadata = _runtime_metadata(event.get("tool_response"))
        if not metadata:
            return None
        activity = metadata.get("activity")
        action = metadata.get("action_outcome")
        completed_action_id = None
        if (
            isinstance(action, Mapping)
            and action.get("status") == "completed"
            and action.get("action_item_id")
        ):
            completed_action_id = str(action["action_item_id"])
        return ToolOutcome(
            activity=activity if isinstance(activity, Mapping) else None,
            completed_action_id=completed_action_id,
        )


def _runtime_metadata(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        direct = value.get("team0_runtime")
        if isinstance(direct, Mapping):
            return direct
        content = value.get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, Mapping) or item.get("type") != "text":
                    continue
                found = _runtime_metadata(item.get("text"))
                if found:
                    return found
    if isinstance(value, str) and len(value) <= 50_000:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return _runtime_metadata(parsed)
    return None
