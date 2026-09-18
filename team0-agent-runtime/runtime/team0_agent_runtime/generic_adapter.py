"""Reusable middleware for Python agent loops."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Mapping, Sequence, Union

from .capabilities import HostCapabilities, RuntimeFeatures
from .runtime import Team0AgentRuntime
from .turn_context import AgentTurnContext


@dataclass(frozen=True)
class AgentTurnInput:
    """Host-owned turn input with Team0 policy and data kept structurally separate."""

    session_id: str
    turn_id: str
    user_message: str
    team0_context: AgentTurnContext | None
    context_warning: str | None = None

    @property
    def team0_policy(self) -> str:
        return self.team0_context.policy if self.team0_context else ""

    @property
    def team0_data(self) -> str:
        return self.team0_context.data if self.team0_context else ""

    def combined_team0_context(self) -> str:
        """Compatibility fallback for hosts with one trusted context channel."""

        return self.team0_context.render() if self.team0_context else ""


@dataclass(frozen=True)
class AgentTurnOutput:
    """The visible agent response and optional bounded tool-result envelopes."""

    message: str
    tool_events: Sequence[Mapping[str, object]] = field(default_factory=tuple)


@dataclass(frozen=True)
class AgentTurnExecution:
    """Completed host turn plus non-blocking Team0 synchronization warnings."""

    message: str
    context_warning: str | None = None
    tool_warnings: tuple[str, ...] = ()
    contribution_warning: str | None = None

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(
            warning
            for warning in (
                self.context_warning,
                *self.tool_warnings,
                self.contribution_warning,
            )
            if warning
        )


TurnCallback = Callable[[AgentTurnInput], Union[str, AgentTurnOutput]]
AsyncTurnCallback = Callable[
    [AgentTurnInput], Awaitable[Union[str, AgentTurnOutput]]
]


class GenericAgentAdapter:
    """Wrap a host agent loop without importing any vendor SDK."""

    def __init__(
        self,
        runtime: Team0AgentRuntime,
        *,
        capabilities: HostCapabilities | None = None,
        features: RuntimeFeatures | None = None,
    ) -> None:
        self.runtime = runtime
        self.capabilities = capabilities or HostCapabilities.custom_agent_loop()
        self.features = features or RuntimeFeatures()
        if not self.capabilities.turn_lifecycle:
            raise ValueError("GenericAgentAdapter requires before/after turn lifecycle support.")

    def capability_manifest(self) -> Mapping[str, object]:
        return self.capabilities.manifest(self.features)

    def run_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        user_message: str,
        invoke: TurnCallback,
    ) -> AgentTurnExecution:
        """Run one synchronous host turn with fail-open Team0 synchronization."""

        turn_input = self._prepare(session_id, turn_id, user_message)
        output = _coerce_output(invoke(turn_input))
        return self._finish(turn_input, output)

    async def run_turn_async(
        self,
        *,
        session_id: str,
        turn_id: str,
        user_message: str,
        invoke: AsyncTurnCallback,
    ) -> AgentTurnExecution:
        """Run one asynchronous host turn with the same lifecycle contract."""

        turn_input = self._prepare(session_id, turn_id, user_message)
        output = _coerce_output(await invoke(turn_input))
        return self._finish(turn_input, output)

    def _prepare(
        self, session_id: str, turn_id: str, user_message: str
    ) -> AgentTurnInput:
        if not session_id.strip() or not turn_id.strip():
            raise ValueError("session_id and turn_id must be stable, non-empty host identifiers.")
        prepared, warning = self.runtime.prepare_turn(
            session_id=session_id,
            turn_id=turn_id,
            prompt=user_message,
        )
        return AgentTurnInput(
            session_id=session_id,
            turn_id=turn_id,
            user_message=user_message,
            team0_context=prepared,
            context_warning=warning,
        )

    def _finish(
        self, turn_input: AgentTurnInput, output: AgentTurnOutput
    ) -> AgentTurnExecution:
        tool_warnings = tuple(
            warning
            for event in output.tool_events
            if (warning := self.runtime.after_tool(event))
        )
        contribution_warning = self.runtime.after_turn(
            turn_id=turn_input.turn_id,
            assistant_message=output.message,
        )
        return AgentTurnExecution(
            message=output.message,
            context_warning=turn_input.context_warning,
            tool_warnings=tool_warnings,
            contribution_warning=contribution_warning,
        )


def _coerce_output(value: str | AgentTurnOutput) -> AgentTurnOutput:
    if isinstance(value, AgentTurnOutput):
        return value
    if isinstance(value, str):
        return AgentTurnOutput(message=value)
    raise TypeError("Agent callback must return str or AgentTurnOutput.")
