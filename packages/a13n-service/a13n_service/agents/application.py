"""Explicit Agent management use-case composition."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.temporal import Clock, utc_now
from a13n_service.web.registry import WebProviderRegistry, built_in_web_provider_registry

from .builtins import BuiltinAgents
from .commands import AgentCommands
from .duplication import AgentDuplication
from .images import AgentImages
from .invocation_resolution import AgentInvocationResolver
from .queries import AgentQueries
from .resolution import AgentResolver
from .revisions import AgentRevisions
from .toolset_service import ToolsetService


class AgentManagement:
    """Complete Agent management surface grouped by cohesive use case."""

    __slots__ = (
        "builtins",
        "commands",
        "duplication",
        "images",
        "invocations",
        "queries",
        "revisions",
        "toolsets",
    )

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        resolver: AgentResolver,
        invocation_resolver: AgentInvocationResolver,
        web_providers: WebProviderRegistry | None = None,
        *,
        clock: Clock = utc_now,
    ) -> None:
        queries = AgentQueries(sessions)
        self.invocations = invocation_resolver
        self.queries = queries
        self.images = AgentImages(sessions)
        self.commands = AgentCommands(sessions, resolver, invocation_resolver, queries, clock=clock)
        self.revisions = AgentRevisions(sessions, resolver, invocation_resolver, queries, clock=clock)
        self.duplication = AgentDuplication(sessions, invocation_resolver, queries, clock=clock)
        self.builtins = BuiltinAgents(sessions, resolver, clock=clock)
        self.toolsets = ToolsetService(sessions, web_providers or built_in_web_provider_registry())
