"""Environment control-plane construction."""

from __future__ import annotations

from dataclasses import dataclass

from a13n_service.agents.environment_resolution import AgentEnvironmentSelectionResolver
from a13n_service.environments.catalog import AttachmentProviderCatalog
from a13n_service.environments.service import EnvironmentManagementService
from a13n_service.process.components import Components
from a13n_service.process.runtime import SharedRuntime


@dataclass(frozen=True, slots=True)
class _EnvironmentBundle:
    service: EnvironmentManagementService
    agent_selection: AgentEnvironmentSelectionResolver


def build_environment_bundle(
    components: Components,
    shared: SharedRuntime,
    catalog: AttachmentProviderCatalog,
) -> _EnvironmentBundle:
    """Construct environment management and Agent selection from one catalog."""

    return _EnvironmentBundle(
        service=EnvironmentManagementService(
            shared.storage.sessions,
            catalog,
            attachment_tester=components.environment_attachment_tester,
        ),
        agent_selection=AgentEnvironmentSelectionResolver(shared.storage.sessions, catalog),
    )


__all__ = ["build_environment_bundle"]
