"""What the worker binds to the Harness for one attempt, for every agent of the run's inline graph.

`resolve_host` reads, in the attempt's short plan session, what each agent uses from the host: its
connections, web providers and pinned skills, and the run's mounted memories, which only the root agent uses.
`open_host` opens them outside any session for one Harness run. The opened `Host` answers the definition
builder with each agent's capabilities and gives each agent's run bindings what it binds of its own. Agents
are keyed by revision, which fixes their configuration.
"""

from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, replace
from functools import partial

from a13n_harness import AgentContext, RunBindings
from a13n_harness.capabilities import FileToolKey, MemoryCursors, RecordToolKey
from a13n_harness.capabilities.web import WebBinding
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.resources.agents.toolsets import (
    enabled_tool,
    enabled_tools,
    memory_file_tools,
    memory_record_tools,
    web_tools,
)
from a13n_service.resources.connections.runtime import SelectedConnection, open_connections, resolve_connections
from a13n_service.runs.agent import ResolvedAgent, media_understanding
from a13n_service.runs.assets import AssetsCapability
from a13n_service.runs.attempts import Lease, prove
from a13n_service.runs.calls import CallCheck
from a13n_service.runs.configuration import ConfigurationCapability
from a13n_service.runs.memories.execution import (
    PlannedMemory,
    file_memory,
    record_memory_capability,
    resolve_memories,
)
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.skills import PinnedSkill, resolve_skills, skills_capability
from a13n_service.runs.tables import RunRow
from a13n_service.runs.web import ResolvedWeb, open_web, resolve_web
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope


@dataclass(frozen=True, slots=True)
class _Parts:
    connections: tuple[SelectedConnection, ...]
    web: ResolvedWeb | None
    skills: tuple[PinnedSkill, ...]


@dataclass(frozen=True, slots=True)
class HostPlan:
    parts: Mapping[str, _Parts]
    memories: tuple[PlannedMemory, ...]
    # The root agent's enabled memory tools of each kind.
    file_tools: frozenset[FileToolKey]
    record_tools: frozenset[RecordToolKey]


async def resolve_host(
    session: AsyncSession,
    run: RunRow,
    principal: Principal,
    scope: WorkspaceScope,
    agent: ResolvedAgent,
    *,
    authority: ExecutionAuthority,
) -> HostPlan:
    parts: dict[str, _Parts] = {}
    for node in agent.agents():
        if node.revision_id in parts:
            continue
        config = node.config
        parts[node.revision_id] = _Parts(
            connections=await resolve_connections(
                session,
                principal,
                scope,
                config.connection_tools,
                run.mcp_headers,
                authority=authority,
            ),
            web=await resolve_web(session, principal, scope, web_tools(config.toolsets), authority=authority),
            skills=await resolve_skills(session, run, config.skills),
        )
    return HostPlan(
        parts,
        memories=await resolve_memories(session, run),
        file_tools=memory_file_tools(agent.config.toolsets),
        record_tools=memory_record_tools(agent.config.toolsets),
    )


@dataclass(frozen=True, slots=True)
class Host:
    runtime: Runtime
    lease: Lease
    principal: Principal
    authority: ExecutionAuthority
    plan: HostPlan
    models: Mapping[str, Model]
    tools: Mapping[str, tuple[AbstractCapability[AgentContext], ...]]
    webs: Mapping[str, WebBinding]
    # The root agent's file and record memory capabilities, for the kinds the run mounts.
    memory: tuple[AbstractCapability[AgentContext], ...]

    def capabilities(self, agent: ResolvedAgent) -> list[AbstractCapability[AgentContext]]:
        """The agent's own host tools: its connections, its pinned skills, asset publishing and configuration."""
        features = list(self.tools[agent.revision_id])
        if skills := self.plan.parts[agent.revision_id].skills:
            features.append(
                skills_capability(
                    skills,
                    runtime=self.runtime,
                    workspace_id=self.lease.workspace_id,
                    prove_lease=partial(prove, self.runtime.storage, self.lease),
                )
            )
        if enabled_tool(agent.config.toolsets, "assets", "publish") is not None:
            features.append(AssetsCapability(self.runtime, self.lease, self.principal, self.authority))
        if configuration := enabled_tools(agent.config.toolsets, "configuration"):
            features.append(
                ConfigurationCapability(self.runtime, self.lease, self.principal, self.authority, configuration)
            )
        return features

    def bindings(self, agent: ResolvedAgent, given: RunBindings) -> RunBindings:
        """`given` with what the agent binds of its own: its web backends and its media understanding."""
        return replace(
            given,
            web=self.webs.get(agent.revision_id),
            file_media_understanding=media_understanding(agent, self.models) if agent.media else None,
        )


@asynccontextmanager
async def open_host(
    runtime: Runtime,
    lease: Lease,
    check: CallCheck,
    plan: HostPlan,
    models: Mapping[str, Model],
    *,
    principal: Principal,
    authority: ExecutionAuthority,
    cursors: MemoryCursors,
) -> AsyncIterator[Host]:
    """Connections, web backends and record memory stores stay open until the context exits; every paid call
    passes `check`. The file memory records the context it delivers in `cursors`."""
    async with AsyncExitStack() as stack:
        tools: dict[str, tuple[AbstractCapability[AgentContext], ...]] = {}
        webs: dict[str, WebBinding] = {}
        for revision_id, parts in plan.parts.items():
            tools[revision_id] = await stack.enter_async_context(
                open_connections(
                    parts.connections,
                    check.connection_call,
                    storage=runtime.storage,
                    redis=runtime.redis,
                    keys=runtime.keys,
                    registry=runtime.registry,
                    policy=runtime.endpoint_policy,
                    settings=runtime.settings.providers,
                )
            )
            if parts.web is not None:
                webs[revision_id] = await stack.enter_async_context(open_web(parts.web, check, runtime=runtime))
        files = file_memory(
            runtime.storage,
            runtime.settings.memory,
            plan.memories,
            lease=lease,
            principal=principal,
            authority=authority,
            cursors=cursors,
            tools=plan.file_tools,
        )
        records = await stack.enter_async_context(
            record_memory_capability(
                runtime, plan.memories, lease=lease, principal=principal, authority=authority, tools=plan.record_tools
            )
        )
        memory = tuple(capability for capability in (files, records) if capability is not None)
        yield Host(runtime, lease, principal, authority, plan, models, tools, webs, memory)
