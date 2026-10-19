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

from a13n_harness import AgentContext, EnvironmentMount, RunBindings, RunConfiguration
from a13n_harness.capabilities import FileToolKey, MemoryCursors, RecordToolKey
from a13n_harness.capabilities.web import WebBinding
from a13n_harness.environment.advanced import EnvironmentRuntime, create_environment_runtime
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import ServiceError
from a13n_service.resources.agents.schemas import PlatformSelection, SharedSkillSync
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
from a13n_service.runs.environments.mounts import PRIMARY
from a13n_service.runs.findings.tools import FindingCapability
from a13n_service.runs.memories.execution import (
    PlannedMemory,
    file_memory,
    record_memory_capability,
    resolve_memories,
)
from a13n_service.runs.model_context import ModelContext
from a13n_service.runs.platform_files import SOURCE
from a13n_service.runs.platform_tools import platform_capability
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.skill_cache import Cache
from a13n_service.runs.skills import PinnedSkill, resolve_skills, skills_capability
from a13n_service.runs.tables import RunRow
from a13n_service.runs.web import ResolvedWeb, open_web, resolve_web
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope


@dataclass(frozen=True, slots=True)
class _Parts:
    connections: tuple[SelectedConnection, ...]
    web: ResolvedWeb | None
    skills: tuple[PinnedSkill, ...]
    platform: PlatformSelection
    builtin_skill: bool


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
                tuple(
                    selection.model_copy(update={"defer_loading": False})
                    if config.tool_search is not None and selection.connection_id in config.tool_search.connection_ids
                    else selection
                    for selection in config.connection_tools
                ),
                run.mcp_headers,
                authority=authority,
            ),
            web=await resolve_web(session, principal, scope, web_tools(config.toolsets), authority=authority),
            skills=await resolve_skills(session, run, config.skills, sync=config.skill_sync),
            platform=config.platform_tools,
            builtin_skill="platform-operations" in config.builtin_skills,
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
    skill_cache: Cache
    tools: Mapping[str, Mapping[str, AbstractCapability[AgentContext]]]
    webs: Mapping[str, WebBinding]
    # The root agent's file and record memory capabilities, for the kinds the run mounts.
    memory: tuple[AbstractCapability[AgentContext], ...]
    environment: EnvironmentRuntime

    def capabilities(self, agent: ResolvedAgent) -> list[AbstractCapability[AgentContext]]:
        """The agent's own host tools: its connections, its pinned skills, asset publishing and configuration."""
        features = list(self.sources(agent).values())
        parts = self.plan.parts[agent.revision_id]
        skills = parts.skills
        sync = agent.config.skill_sync
        if parts.builtin_skill or skills or (isinstance(sync, SharedSkillSync) and sync.cleanup):
            features.append(
                skills_capability(
                    skills,
                    cache=self.skill_cache,
                    builtin=SOURCE if parts.builtin_skill else None,
                    sync=sync,
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
        trace_tools = enabled_tools(agent.config.toolsets, "traces")
        finding_tools = enabled_tools(agent.config.toolsets, "findings")
        if trace_tools or finding_tools:
            features.append(
                FindingCapability(self.runtime, self.lease, self.principal, self.authority, trace_tools, finding_tools)
            )
        return features

    def sources(self, agent: ResolvedAgent) -> Mapping[str, AbstractCapability[AgentContext]]:
        """Already-opened connection and platform sources available for explicit grouping."""
        return self.tools[agent.revision_id]

    def bindings(self, agent: ResolvedAgent, given: RunBindings) -> RunBindings:
        """`given` with what the agent binds of its own: its web backends and its media understanding."""
        return replace(
            given,
            model_context=ModelContext(self.lease.run_id),
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
    configuration: RunConfiguration,
    skill_cache: Cache,
    environments: Mapping[str, EnvironmentMount],
) -> AsyncIterator[Host]:
    """Connections, web backends and record memory stores stay open until the context exits; every paid call
    passes `check`. The file memory records the context it delivers in `cursors`."""
    async with AsyncExitStack() as stack:
        platform_mounts = runtime.platform_mounts if any(parts.builtin_skill for parts in plan.parts.values()) else {}
        tools: dict[str, dict[str, AbstractCapability[AgentContext]]] = {}
        webs: dict[str, WebBinding] = {}
        for revision_id, parts in plan.parts.items():
            connections = await stack.enter_async_context(
                open_connections(
                    parts.connections,
                    check.connection_call,
                    storage=runtime.storage,
                    redis=runtime.redis,
                    keys=runtime.keys,
                    registry=runtime.registry,
                    policy=runtime.endpoint_policy.for_run(configuration),
                    settings=runtime.settings.providers,
                )
            )
            tools[revision_id] = dict(zip((item.connection.id for item in parts.connections), connections, strict=True))
            if parts.platform.groups:
                platform = await stack.enter_async_context(platform_capability(runtime, lease, parts.platform))
                tools[revision_id]["platform"] = platform
            if parts.web is not None:
                webs[revision_id] = await stack.enter_async_context(
                    open_web(parts.web, check, runtime=runtime, configuration=configuration)
                )
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
                runtime,
                plan.memories,
                lease=lease,
                principal=principal,
                authority=authority,
                tools=plan.record_tools,
                configuration=configuration,
            )
        )
        memory = tuple(capability for capability in (files, records) if capability is not None)
        yield Host(
            runtime,
            lease,
            principal,
            authority,
            plan,
            models,
            skill_cache,
            tools,
            webs,
            memory,
            _environment(environments, platform_mounts),
        )


def _environment(user: Mapping[str, EnvironmentMount], builtin: Mapping[str, EnvironmentMount]) -> EnvironmentRuntime:
    """Preserve the user's default while adding non-default Host document mounts."""
    if user.keys() & builtin.keys():
        raise ServiceError("invalid_argument", "Environment names conflict with builtin Skill mounts")
    default = PRIMARY if PRIMARY in user else next(iter(user)) if len(user) == 1 else None
    return create_environment_runtime(mounts={**user, **builtin}, default_mount=default)
