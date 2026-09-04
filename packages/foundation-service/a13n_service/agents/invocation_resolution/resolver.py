"""Explicit composition of Agent invocation preparation and freezing."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver
from a13n_service.models.runtime import AcceptedModelSelector

from ..domain import PluginRuntimeMode
from ..environment_resolution import AgentEnvironmentSelectionResolver
from ..plugin_resolution import AgentPluginSelectionResolver
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
        plugin_runtime_mode: PluginRuntimeMode,
        environment_resolver: AgentEnvironmentSelectionResolver | None = None,
        plugin_resolver: AgentPluginSelectionResolver | None = None,
        connectivity_resolver: ConnectivitySelectionResolver | None = None,
        protocol_policy: AgentProtocolPolicy | None = None,
    ) -> None:
        plugins = plugin_resolver or AgentPluginSelectionResolver(
            sessions,
            runtime_mode=plugin_runtime_mode,
        )
        policy = protocol_policy or AgentProtocolPolicy()
        self.preparation = AgentInvocationPreparer(
            sessions,
            model_selector,
            plugin_runtime_mode=plugin_runtime_mode,
            environment_resolver=environment_resolver,
            plugin_resolver=plugins,
            connectivity_resolver=connectivity_resolver,
            protocol_policy=policy,
        )
        self.freezing = AgentInvocationFreezer(
            model_selector,
            plugin_runtime_mode=plugin_runtime_mode,
            environment_resolver=environment_resolver,
            plugin_resolver=plugins,
            connectivity_resolver=connectivity_resolver,
        )
