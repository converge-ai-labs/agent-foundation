"""Agent control-plane construction."""

from __future__ import annotations

from a13n_service.agents.application import AgentManagement
from a13n_service.agents.resolution import AgentResolver
from a13n_service.process.agents import AgentResources
from a13n_service.process.components import Components
from a13n_service.process.runtime import SharedRuntime


def build_agent_management(
    components: Components,
    shared: SharedRuntime,
    resources: AgentResources,
) -> AgentManagement:
    """Construct the explicit Agent management use-case surface."""

    resolver = components.agent_resolver or AgentResolver(
        shared.storage.sessions,
        resources.models,
        connectivity_resolver=resources.connectivity,
        web_provider_registry=resources.web_providers,
    )
    return AgentManagement(
        shared.storage.sessions,
        resolver,
        resources.invocations,
        resources.web_providers,
    )


__all__ = ["build_agent_management"]
