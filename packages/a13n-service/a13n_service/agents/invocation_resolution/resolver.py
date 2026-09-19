"""Explicit composition of Agent invocation preparation and freezing."""

from __future__ import annotations

from a13n_harness.memory_plugins import MemoryBackendCatalog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.web.registry import WebProviderRegistry, built_in_web_provider_registry

from ..validation import AgentProtocolPolicy
from .freezing import AgentInvocationFreezer
from .preparation import AgentInvocationPreparer


class AgentInvocationResolver:
    """Two-phase invocation resolution with explicit transaction boundaries."""

    __slots__ = ("freezing", "preparation")

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        model_selector: AcceptedModelSelector,
        *,
        connectivity_resolver: ConnectivitySelectionResolver | None = None,
        protocol_policy: AgentProtocolPolicy | None = None,
        web_provider_registry: WebProviderRegistry | None = None,
        memory_backend_catalog: MemoryBackendCatalog | None = None,
    ) -> None:
        policy = protocol_policy or AgentProtocolPolicy()
        connectivity = connectivity_resolver or ConnectivitySelectionResolver(sessions)
        web = web_provider_registry or built_in_web_provider_registry()
        memory = memory_backend_catalog if memory_backend_catalog is not None else MemoryBackendCatalog()
        self.preparation = AgentInvocationPreparer(
            sessions,
            model_selector,
            connectivity_resolver=connectivity,
            protocol_policy=policy,
            web_provider_registry=web,
            memory_backend_catalog=memory,
        )
        self.freezing = AgentInvocationFreezer(
            model_selector,
            connectivity_resolver=connectivity,
            web_provider_registry=web,
            memory_backend_catalog=memory,
        )
