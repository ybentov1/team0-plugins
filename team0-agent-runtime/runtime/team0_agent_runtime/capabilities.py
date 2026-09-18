"""Host-neutral capability negotiation for connected agents.

Capability support describes what an agent host can technically deliver. It never
grants access; Team0's saved server-side agent grant remains the authority source.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


CAPABILITY_CONTRACT = "team0.connected_agent_capabilities.v1"


@dataclass(frozen=True)
class CapabilitySupport:
    status: str
    mode: str
    reason: str


@dataclass(frozen=True)
class RuntimeFeatures:
    """Team0 bridges shipped by this runtime release."""

    brain: bool = True
    meetings: bool = False
    agent_network: bool = True
    proactive: bool = False


@dataclass(frozen=True)
class HostCapabilities:
    """Technical surfaces exposed by an agent host or wrapper."""

    turn_lifecycle: bool = False
    callable_tools: bool = False
    persistent_instructions: bool = False
    inbound_events: bool = False
    callable_agent: bool = False
    presents_agent_identity: bool = False

    @classmethod
    def custom_agent_loop(
        cls,
        *,
        callable_tools: bool = False,
        inbound_events: bool = False,
        callable_agent: bool = False,
        presents_agent_identity: bool = False,
    ) -> "HostCapabilities":
        """Describe an agent loop wrapped at its before/after turn boundaries."""

        return cls(
            turn_lifecycle=True,
            callable_tools=callable_tools,
            inbound_events=inbound_events,
            callable_agent=callable_agent,
            presents_agent_identity=presents_agent_identity,
        )

    @classmethod
    def mcp_only(cls) -> "HostCapabilities":
        """Describe a host where tools exist but turn boundaries are unavailable."""

        return cls(callable_tools=True)

    @classmethod
    def instruction_guided_tools(cls) -> "HostCapabilities":
        """Describe a host that can persist guidance around model-called tools."""

        return cls(callable_tools=True, persistent_instructions=True)

    def host_support(self) -> Mapping[str, CapabilitySupport]:
        """Map host mechanics to possible product delivery modes."""

        if self.turn_lifecycle:
            brain = CapabilitySupport(
                "available", "automatic", "The host exposes verified before/after turn boundaries."
            )
        elif self.callable_tools and self.persistent_instructions:
            brain = CapabilitySupport(
                "partial",
                "instruction_guided",
                "Persistent host instructions can guide Team0 use, but the host does not expose verified turn boundaries.",
            )
        elif self.callable_tools:
            brain = CapabilitySupport(
                "partial", "on_demand", "The agent may call Team0, but automatic learning is not guaranteed."
            )
        else:
            brain = CapabilitySupport(
                "unavailable", "none", "The host exposes neither lifecycle callbacks nor callable tools."
            )

        if self.callable_agent and self.presents_agent_identity:
            meetings = CapabilitySupport(
                "available", "agent_participant", "Team0 can invoke and present the connected agent for a meeting."
            )
        elif self.callable_agent:
            meetings = CapabilitySupport(
                "partial", "team0_notetaker_bridge", "The agent can process the meeting, but Team0 remains the visible participant."
            )
        else:
            meetings = CapabilitySupport(
                "unavailable", "none", "The host does not expose a callable agent runtime."
            )

        if self.callable_tools and self.inbound_events:
            network = CapabilitySupport(
                "available", "push_and_pull", "The agent can send and receive governed Agent Network messages."
            )
        elif self.callable_tools:
            network = CapabilitySupport(
                "partial", "polling", "The agent can send messages and poll its governed inbox."
            )
        elif self.inbound_events:
            network = CapabilitySupport(
                "partial", "receive_only", "Team0 can deliver events, but the host exposes no callable reply surface."
            )
        else:
            network = CapabilitySupport(
                "unavailable", "none", "The host exposes neither callable tools nor an inbound event receiver."
            )

        if self.inbound_events:
            proactive = CapabilitySupport(
                "available", "push", "Team0 can deliver a bounded event to the connected agent."
            )
        elif self.callable_tools:
            proactive = CapabilitySupport(
                "partial", "pull", "The agent may poll for events, but Team0 cannot wake it."
            )
        else:
            proactive = CapabilitySupport(
                "unavailable", "none", "The host exposes no event delivery or polling surface."
            )

        return {
            "brain": brain,
            "meetings": meetings,
            "agent_network": network,
            "proactive": proactive,
        }

    def product_support(
        self, features: RuntimeFeatures | None = None
    ) -> Mapping[str, CapabilitySupport]:
        """Intersect host support with bridges shipped by the Team0 runtime."""

        shipped = features or RuntimeFeatures()
        return {
            name: _runtime_gate(name, support, bool(getattr(shipped, name)))
            for name, support in self.host_support().items()
        }

    def manifest(
        self, features: RuntimeFeatures | None = None
    ) -> Mapping[str, Any]:
        """Return a JSON-safe discovery document for installers and product UI."""

        shipped = features or RuntimeFeatures()
        return {
            "contract": CAPABILITY_CONTRACT,
            "host": asdict(self),
            "runtime_features": asdict(shipped),
            "host_support": {
                name: asdict(support)
                for name, support in self.host_support().items()
            },
            "product_support": {
                name: asdict(support)
                for name, support in self.product_support(shipped).items()
            },
            "authority": {
                "source": "server_saved_agent_grant",
                "support_is_not_authorization": True,
                "host_declaration_may_widen_access": False,
            },
        }


def _runtime_gate(
    name: str, support: CapabilitySupport, shipped: bool
) -> CapabilitySupport:
    if shipped or support.status == "unavailable":
        return support
    return CapabilitySupport(
        "partial",
        "adapter_pending",
        f"The host supports {name}, but this Team0 runtime does not ship that bridge yet.",
    )
