from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "runtime"))

from team0_agent_runtime import (
    AgentTurnContext,
    AgentTurnOutput,
    CAPABILITY_CONTRACT,
    GenericAgentAdapter,
    HostCapabilities,
    RuntimeFeatures,
)


class RuntimeDouble:
    def __init__(self, *, context_warning=None, tool_warning=None, turn_warning=None):
        self.context_warning = context_warning
        self.tool_warning = tool_warning
        self.turn_warning = turn_warning
        self.prepared = []
        self.tools = []
        self.completed = []

    def prepare_turn(self, *, session_id, turn_id, prompt):
        self.prepared.append((session_id, turn_id, prompt))
        return (
            AgentTurnContext(
                read_id="read_1",
                read_status="completed",
                policy="trusted policy",
                data="retrieved data",
            ),
            self.context_warning,
        )

    def after_tool(self, event):
        self.tools.append(event)
        return self.tool_warning

    def after_turn(self, *, turn_id, assistant_message):
        self.completed.append((turn_id, assistant_message))
        return self.turn_warning


def test_capability_manifest_separates_support_from_authority():
    capabilities = HostCapabilities.custom_agent_loop(callable_tools=True)

    manifest = capabilities.manifest()

    assert manifest["contract"] == CAPABILITY_CONTRACT
    assert manifest["product_support"]["brain"]["mode"] == "automatic"
    assert manifest["product_support"]["agent_network"]["mode"] == "polling"
    assert manifest["host_support"]["proactive"]["mode"] == "pull"
    assert manifest["product_support"]["proactive"]["mode"] == "adapter_pending"
    assert manifest["product_support"]["meetings"]["status"] == "unavailable"
    assert manifest["authority"] == {
        "source": "server_saved_agent_grant",
        "support_is_not_authorization": True,
        "host_declaration_may_widen_access": False,
    }


def test_full_host_profile_does_not_overclaim_unshipped_team0_bridges():
    capabilities = HostCapabilities.custom_agent_loop(
        callable_tools=True,
        inbound_events=True,
        callable_agent=True,
        presents_agent_identity=True,
    )

    assert {name: value.status for name, value in capabilities.host_support().items()} == {
        "brain": "available",
        "meetings": "available",
        "agent_network": "available",
        "proactive": "available",
    }
    product = capabilities.product_support()
    assert product["meetings"].mode == "adapter_pending"
    assert product["proactive"].mode == "adapter_pending"
    assert product["brain"].status == "available"
    assert product["agent_network"].status == "available"


def test_runtime_can_advertise_a_bridge_only_after_it_is_shipped():
    capabilities = HostCapabilities.custom_agent_loop(
        inbound_events=True,
        callable_agent=True,
        presents_agent_identity=True,
    )

    support = capabilities.product_support(RuntimeFeatures(
        meetings=True,
        proactive=True,
    ))

    assert support["meetings"].mode == "agent_participant"
    assert support["proactive"].mode == "push"


def test_sync_adapter_keeps_context_separate_and_completes_lifecycle():
    runtime = RuntimeDouble(tool_warning="tool delayed")
    adapter = GenericAgentAdapter(runtime)  # type: ignore[arg-type]

    def invoke(turn):
        assert turn.team0_policy == "trusted policy"
        assert turn.team0_data == "retrieved data"
        assert "Trusted runtime policy" in turn.combined_team0_context()
        return AgentTurnOutput(
            message="Done",
            tool_events=({"tool_name": "linear", "tool_response": {}},),
        )

    result = adapter.run_turn(
        session_id="session-1",
        turn_id="turn-1",
        user_message="What next?",
        invoke=invoke,
    )

    assert result.message == "Done"
    assert result.warnings == ("tool delayed",)
    assert runtime.prepared == [("session-1", "turn-1", "What next?")]
    assert runtime.tools == [{"tool_name": "linear", "tool_response": {}}]
    assert runtime.completed == [("turn-1", "Done")]


def test_host_failure_is_not_contributed_as_a_completed_turn():
    runtime = RuntimeDouble()
    adapter = GenericAgentAdapter(runtime)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="host failed"):
        adapter.run_turn(
            session_id="session-1",
            turn_id="turn-1",
            user_message="Continue",
            invoke=lambda _: (_ for _ in ()).throw(RuntimeError("host failed")),
        )

    assert runtime.completed == []


def test_async_adapter_uses_the_same_contract():
    runtime = RuntimeDouble(context_warning="context delayed", turn_warning="write delayed")
    adapter = GenericAgentAdapter(runtime)  # type: ignore[arg-type]

    async def invoke(turn):
        return f"Used {turn.team0_data}"

    result = asyncio.run(adapter.run_turn_async(
        session_id="session-async",
        turn_id="turn-async",
        user_message="Help",
        invoke=invoke,
    ))

    assert result.message == "Used retrieved data"
    assert result.warnings == ("context delayed", "write delayed")
    assert runtime.completed == [("turn-async", "Used retrieved data")]


def test_generic_adapter_requires_stable_host_turn_identity():
    adapter = GenericAgentAdapter(RuntimeDouble())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="stable, non-empty"):
        adapter.run_turn(
            session_id="session-1",
            turn_id=" ",
            user_message="Hello",
            invoke=lambda _: "Hi",
        )


def test_mcp_only_profile_cannot_be_misreported_as_an_automatic_brain():
    capabilities = HostCapabilities.mcp_only()

    assert capabilities.product_support()["brain"].mode == "on_demand"
    with pytest.raises(ValueError, match="requires before/after turn"):
        GenericAgentAdapter(RuntimeDouble(), capabilities=capabilities)  # type: ignore[arg-type]


def test_persistent_instructions_improve_mcp_activation_without_claiming_lifecycle():
    capabilities = HostCapabilities.instruction_guided_tools()

    manifest = capabilities.manifest()

    assert manifest["host"]["persistent_instructions"] is True
    assert manifest["product_support"]["brain"] == {
        "status": "partial",
        "mode": "instruction_guided",
        "reason": (
            "Persistent host instructions can guide Team0 use, but the host does not "
            "expose verified turn boundaries."
        ),
    }
    with pytest.raises(ValueError, match="requires before/after turn"):
        GenericAgentAdapter(RuntimeDouble(), capabilities=capabilities)  # type: ignore[arg-type]
