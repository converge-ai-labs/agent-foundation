"""Agent control-plane construction."""

from __future__ import annotations

from a13n_service.agents.application import AgentManagement
from a13n_service.process.agents import AgentResources, build_agent_resolver
from a13n_service.process.components import Components
from a13n_service.process.runtime import SharedRuntime


def build_agent_management(
    components: Components,
    shared: SharedRuntime,
    resources: AgentResources,
) -> AgentManagement:
    """Construct the explicit Agent management use-case surface."""

    resolver = build_agent_resolver(components, shared, resources)
    return AgentManagement(
        shared.storage.sessions,
        resolver,
        resources.invocations,
        resources.models,
        resources.web_providers,
    )


__all__ = ["build_agent_management"]
