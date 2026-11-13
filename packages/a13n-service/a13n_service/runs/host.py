"""What the worker binds to the Harness for one attempt, for every agent of the run's inline graph.

`resolve_host` reads, in the attempt's short plan session, what each agent uses from the host: its
connections, web providers and pinned skills, and the run's mounted memories, which only the root agent uses.
`open_host` opens them outside any session for one Harness run. The opened `Host` answers the definition
builder with each agent's capabilities and gives each agent's run bindings what it binds of its own. Agents
are keyed by revision, which fixes their configuration.
"""

from collections.abc import AsyncGenerator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, replace
from functools import partial

from a13n_harness import AgentContext, EnvironmentMount, RequestContext, RunBindings, RunConfiguration
from a13n_harness.capabilities import FileToolKey, MemoryCursors, RecordToolKey
from a13n_harness.capabilities.browser import BrowserUseCapability
from a13n_harness.capabilities.context import RuntimeContextCapability, RuntimeContextConfiguration
from a13n_harness.capabilities.tool_execution import ToolExecutionCapability
from a13n_harness.capabilities.web import WebBinding
from a13n_harness.environment.advanced import EnvironmentRuntime, create_environment_runtime
from a13n_harness.tools import ToolIdentity
from pydantic_ai.capabilities import AbstractCapability, CombinedCapability
from pydantic_ai.models import Model
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.execution_policy import ExecutionPolicy
from a13n_service.infra.errors import ServiceError, invalid
from a13n_service.resources.agents import instructions, stored
from a13n_service.resources.agents.schemas import SharedSkillSync
from a13n_service.resources.agents.tables import AgentRevisionRow, AgentRow
from a13n_service.resources.agents.toolsets import (
    enabled_tool,
    enabled_tools,
    memory_file_tools,
    memory_record_tools,
    platform_tools,
    web_tools,
)
from a13n_service.resources.connections.runtime import SelectedConnection, open_connections, resolve_connections
from a13n_service.runs.agent import ResolvedAgent, media_understanding
from a13n_service.runs.assets import AssetsCapability
from a13n_service.runs.attempts import Lease
from a13n_service.runs.browsers.admission import GUIDANCE as BROWSER_GUIDANCE
from a13n_service.runs.browsers.context import BrowserContextCapability
from a13n_service.runs.browsers.execution import Backend as BrowserBackend
from a13n_service.runs.browsers.execution import open_backend
from a13n_service.runs.browsers.schemas import BrowserBinding
from a13n_service.runs.builtin_instructions import BuiltinInstructionsCapability
from a13n_service.runs.calls import CallCheck
from a13n_service.runs.environments.desktop.freshness import Freshness
from a13n_service.runs.environments.desktop.notify import HandbackNotifications
from a13n_service.runs.environments.egress.instructions import CredentialInstructionsCapability
from a13n_service.runs.environments.egress.instructions import render as credential_instructions
from a13n_service.runs.environments.mounts import PRIMARY
from a13n_service.runs.mcp_apps.capture import Captures
from a13n_service.runs.memories.execution import (
    PlannedMemory,
    file_memory,
    record_memory_capability,
    resolve_memories,
)
from a13n_service.runs.model_context import ModelContext
from a13n_service.runs.object_store import ThreadObjectStore
from a13n_service.runs.platform_files import SOURCE
from a13n_service.runs.platform_tools import platform_capability
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EnvironmentMount as FrozenEnvironmentMount
from a13n_service.runs.schemas import RunOptions
from a13n_service.runs.skill_cache import Cache
from a13n_service.runs.skills import SkillPackage, resolve_skills, skills_capability
from a13n_service.runs.tables import RunRow
from a13n_service.runs.tool_executions.authority import prove
from a13n_service.runs.web import ResolvedWeb, open_web, resolve_web
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope


@dataclass(frozen=True, slots=True)
class _Parts:
    connections: tuple[SelectedConnection, ...]
    web: ResolvedWeb | None
    skills: tuple[SkillPackage, ...]
    platform: Mapping[str, ToolIdentity]
    host_skills: bool
    builtin_instructions: str | None
    browser_tools: frozenset[str]
    assets: bool
    computer_notify: bool


@dataclass(frozen=True, slots=True)
class HostPlan:
    parts: Mapping[str, _Parts]
    root_definition_id: str
    execution: ExecutionPolicy
    memories: tuple[PlannedMemory, ...]
    # The root agent's enabled memory tools of each kind.
    file_tools: frozenset[FileToolKey]
    record_tools: frozenset[RecordToolKey]
    browser: BrowserBinding | None
    credential_instructions: str | None


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
    # Resolve server-owned preset identities for the whole inline graph. Custom copies receive no guidance.
    builtin = dict(
        (
            await session.execute(
                select(AgentRevisionRow.id, AgentRow.preset_kind)
                .join(AgentRow, AgentRow.id == AgentRevisionRow.agent_id)
                .where(
                    AgentRevisionRow.workspace_id == scope.workspace_id,
                    AgentRevisionRow.id.in_({node.revision_id for node in agent.agents()}),
                    AgentRow.source == "builtin",
                )
            )
        )
        .tuples()
        .all()
    )
    for node in agent.agents():
        if node.definition_id in parts:
            continue
        config = node.config
        host_skills = bool(config.builtin_skills)
        skills = await resolve_skills(session, run, config.skills, sync=config.skill_sync)
        # Reject before any shared directory is written, including configurations saved before this check existed.
        if any(skill.name in config.builtin_skills for skill in skills):
            raise invalid("builtin_skills", "a selected workspace skill has the same name as a builtin skill")
        parts[node.definition_id] = _Parts(
            connections=await resolve_connections(
                session,
                principal,
                scope,
                config.connection_tools,
                run.mcp_headers,
                authority=authority,
            ),
            web=await resolve_web(session, principal, scope, web_tools(config.toolsets), authority=authority),
            skills=skills,
            platform=platform_tools(config.toolsets),
            host_skills=host_skills,
            builtin_instructions=instructions.for_preset(
                builtin.get(node.revision_id) if node.revision_id is not None else None
            ),
            browser_tools=enabled_tools(config.toolsets, "browser"),
            computer_notify=enabled_tool(config.toolsets, "computer", "notify") is not None,
            assets=enabled_tool(config.toolsets, "assets", "publish") is not None,
        )
    return HostPlan(
        parts,
        agent.definition_id,
        execution=ExecutionPolicy().overlay(stored.require(RunOptions, run.options, kind="run", id=run.id).execution),
        memories=await resolve_memories(session, run),
        file_tools=memory_file_tools(agent.config.toolsets),
        record_tools=memory_record_tools(agent.config.toolsets),
        browser=BrowserBinding.model_validate(run.browser_binding) if run.browser_binding is not None else None,
        credential_instructions=credential_instructions(
            [FrozenEnvironmentMount.model_validate(mount) for mount in run.environment_mounts]
        ),
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
    browsers: Mapping[str, BrowserBackend]
    environment: EnvironmentRuntime

    def capabilities(
        self, agent: ResolvedAgent, *, retain_tools: bool = False
    ) -> list[AbstractCapability[AgentContext]]:
        """The agent's own host tools: its connections, platform tools, pinned skills and asset publishing."""
        features = list(self.sources(agent).values())
        features.append(Freshness())
        if retain_tools:
            # Only conversation roots retain calls; inline children and memory organizers stay inline.
            features.append(ToolExecutionCapability(self.plan.execution.tools))
        parts = self.plan.parts[agent.definition_id]
        if agent.definition_id in self.browsers:
            features.append(
                BrowserContextCapability(self.runtime, self.lease, self.browsers[agent.definition_id].binding)
            )
        if parts.builtin_instructions is not None:
            features.append(BuiltinInstructionsCapability(parts.builtin_instructions))
        if self.plan.credential_instructions is not None and enabled_tool(agent.config.toolsets, "shell", "exec"):
            features.append(CredentialInstructionsCapability(self.plan.credential_instructions))
        skills = parts.skills
        sync = agent.config.skill_sync
        if parts.host_skills or skills or (isinstance(sync, SharedSkillSync) and sync.cleanup):
            features.append(
                skills_capability(
                    skills,
                    cache=self.skill_cache,
                    builtin=SOURCE if parts.host_skills else None,
                    sync=sync,
                    runtime=self.runtime,
                    workspace_id=self.lease.workspace_id,
                    prove_lease=partial(prove, self.runtime, self.lease),
                )
            )
        return features

    def sources(self, agent: ResolvedAgent) -> Mapping[str, AbstractCapability[AgentContext]]:
        """Already-constructed host sources, shared by direct and grouped presentation."""
        return self.tools[agent.definition_id]

    def bindings(self, agent: ResolvedAgent, given: RunBindings) -> RunBindings:
        """`given` with what the agent binds of its own: its web backends and its media understanding."""
        return replace(
            given,
            model_context=ModelContext(self.lease.run_id, self.runtime.storage),
            web=self.webs.get(agent.definition_id),
            browser_backend=self.browsers.get(agent.definition_id),
            object_store=(
                ThreadObjectStore(self.runtime, self.lease, self.authority)
                if agent.config.toolsets["object_store"].enabled
                else None
            ),
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
    app_captures: Captures | None = None,
    request_context: RequestContext | None = None,
) -> AsyncGenerator[Host]:
    """Connections, web backends and record memory stores stay open until the context exits; every paid call
    passes `check`. The file memory records the context it delivers in `cursors`."""
    async with AsyncExitStack() as stack:
        platform_mounts = runtime.platform_mounts if any(parts.host_skills for parts in plan.parts.values()) else {}
        tools: dict[str, dict[str, AbstractCapability[AgentContext]]] = {}
        webs: dict[str, WebBinding] = {}
        browsers: dict[str, BrowserBackend] = {}
        for definition_id, parts in plan.parts.items():
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
                    capture=partial(app_captures.add, definition_id) if app_captures is not None else None,
                    request_context=request_context,
                )
            )
            tools[definition_id] = dict(
                zip((item.connection.id for item in parts.connections), connections, strict=True)
            )
            if parts.platform:
                platform = await stack.enter_async_context(platform_capability(runtime, lease, parts.platform))
                tools[definition_id]["platform"] = platform
            if plan.browser is not None and parts.browser_tools:
                browsers[definition_id] = await stack.enter_async_context(
                    open_backend(runtime, lease, plan.browser, check, parts.browser_tools)
                )
                tools[definition_id]["browser"] = BrowserUseCapability(
                    enabled_tools=parts.browser_tools,
                    on_human_control=lambda: BROWSER_GUIDANCE,
                    file_instructions=(
                        "Upload workspace assets into an observed file input: action='upload', ref, "
                        "and 1-16 paths in the form asset:<asset_id>. Host or Environment paths are not accepted. "
                        "Only unretired assets in this workspace may be uploaded. "
                        "action='downloads' takes no ref/paths; it publishes completed tracked downloads "
                        "as workspace assets and returns asset IDs, never Host paths. Publication requires "
                        "workspace write authority. File transfer requires a supporting Browser provider."
                    ),
                )
            if parts.computer_notify:
                tools[definition_id]["computer-notify"] = HandbackNotifications(runtime, lease)
            if parts.assets:
                tools[definition_id]["assets"] = AssetsCapability(runtime, lease, principal, authority)
            if parts.web is not None:
                webs[definition_id] = await stack.enter_async_context(
                    open_web(parts.web, check, runtime=runtime, configuration=configuration)
                )
        files = file_memory(
            runtime,
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
        memory: list[AbstractCapability[AgentContext]] = [
            capability for capability in (files, records) if capability is not None
        ]
        if files is not None:
            memory.append(
                RuntimeContextCapability(
                    RuntimeContextConfiguration(
                        include_elapsed_time=False, include_usage=False, include_latest_request_usage=False
                    )
                )
            )
        if memory:
            tools[plan.root_definition_id]["memory"] = CombinedCapability(memory)
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
            browsers,
            _environment(environments, platform_mounts, command_environment=plan.execution.environment),
        )


def _environment(
    user: Mapping[str, EnvironmentMount],
    builtin: Mapping[str, EnvironmentMount],
    *,
    command_environment: Mapping[str, str] | None = None,
) -> EnvironmentRuntime:
    """Preserve the user's default while adding non-default Host document mounts."""
    if user.keys() & builtin.keys():
        raise ServiceError("invalid_argument", "Environment names conflict with builtin Skill mounts")
    default = PRIMARY if PRIMARY in user else next(iter(user)) if len(user) == 1 else None
    return create_environment_runtime(
        mounts={**user, **builtin}, default_mount=default, command_environment=command_environment
    )
