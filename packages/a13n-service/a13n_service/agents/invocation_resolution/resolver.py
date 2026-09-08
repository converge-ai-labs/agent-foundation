"""Explicit composition of Agent invocation preparation and freezing."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver
from a13n_service.models.runtime import AcceptedModelSelector

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
    ) -> None:
        policy = protocol_policy or AgentProtocolPolicy()
        connectivity = connectivity_resolver or ConnectivitySelectionResolver(sessions)
        self.preparation = AgentInvocationPreparer(
            sessions,
            model_selector,
            connectivity_resolver=connectivity,
            protocol_policy=policy,
        )
        self.freezing = AgentInvocationFreezer(
            model_selector,
            connectivity_resolver=connectivity,
        )
