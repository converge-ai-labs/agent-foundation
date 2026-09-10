"""Explicit Agent management use-case composition."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.temporal import Clock, utc_now

from .builtins import BuiltinAgents
from .commands import AgentCommands
from .duplication import AgentDuplication
from .images import AgentImages
from .invocation_resolution import AgentInvocationResolver
from .queries import AgentQueries
from .resolution import AgentResolver
from .revisions import AgentRevisions


class AgentManagement:
    """Complete Agent management surface grouped by cohesive use case."""

    __slots__ = ("builtins", "commands", "duplication", "images", "invocations", "queries", "revisions")

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        resolver: AgentResolver,
        invocation_resolver: AgentInvocationResolver,
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
