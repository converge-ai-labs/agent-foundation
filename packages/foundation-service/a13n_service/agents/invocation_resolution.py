"""Two-phase Agent invocation selection and immutable config freezing."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_agent,
    authorize_workspace,
)
from a13n_service.models.runtime import AcceptedModelSelector, PreparedModelExecution
from a13n_service.models.service import ModelError
from a13n_service.plugins.runtime import PluginRuntimeLockError
from a13n_service.skills.domain import SkillRevisionLock
from a13n_service.storage import short_session

from .domain import (
    AgentRevision,
    AgentRunOverride,
    EffectiveAgentConfig,
    EffectiveAgentModel,
    EnvironmentExecutionConfig,
    PluginRuntimeMode,
    ResolvedPluginVersion,
    ResolvedSkillBinding,
    ResolvedSubagentEdge,
    SkillSelection,
    SubagentSelection,
    canonical_digest,
)
from .environment_resolution import AgentEnvironmentSelectionResolver, PreparedEnvironmentSelection
from .errors import (
    AgentError,
    agent_archived,
    agent_current_revision_missing,
    agent_disabled,
    agent_not_found,
    agent_revision_not_executable,
    agent_revision_not_found,
    current_revision_conflict,
)
from .invocation import AgentRunSensitiveValues, MergedAgentRun, merge_agent_run_override
from .models import AgentRecord, AgentRevisionRecord
from .plugin_resolution import AgentPluginSelectionResolver, PluginSelectionError, PreparedPluginSelections
from .resolution import MAX_SUBAGENT_DEPTH, MAX_SUBAGENT_NODES
from .skill_resolution import (
    PreparedSkillLock,
    SkillSelectionInvalid,
    freeze_skill_locks,
    prepare_skill_locks_from_bindings,
    prepare_skill_locks_from_selections,
)
from .validation import AgentConfigValidationError, AgentProtocolPolicy, validate_agent_config


class AgentSelectorKind(StrEnum):
    current = "current"
    exact = "exact"


class RootAgentStatePolicy(StrEnum):
    invocable = "invocable"
    disabled_allowed = "disabled_allowed"
    archived_allowed = "archived_allowed"


@dataclass(frozen=True, slots=True)
class PreparedInvocationSubagent:
    edge: ResolvedSubagentEdge
    child_revision_digest: str
    child_runtime_lock_digest: str


PreparedInvocationModel = PreparedModelExecution


@dataclass(frozen=True, slots=True)
class PreparedAgentInvocation:
    actor: AuthenticatedActor
    organization_id: str
    workspace_id: str
    agent_id: str
    agent_revision_id: str
    selector_kind: AgentSelectorKind
    expected_current_revision_id: str | None
    revision_content_digest: str
    revision: AgentRevision
    merged: MergedAgentRun
    model: PreparedInvocationModel
    plugins: PreparedPluginSelections | None
    skills: tuple[PreparedSkillLock, ...]
    resolved_plugin_versions: tuple[ResolvedPluginVersion, ...]
    environment: PreparedEnvironmentSelection | None
    resolved_environment: EnvironmentExecutionConfig | None
    subagents: tuple[PreparedInvocationSubagent, ...]


@dataclass(frozen=True, slots=True)
class FrozenAgentInvocation:
    agent_id: str
    agent_revision_id: str
    selector_kind: AgentSelectorKind
    effective_config: EffectiveAgentConfig
    sensitive_values: AgentRunSensitiveValues
    sensitive_values_digest: str


@dataclass(frozen=True, slots=True)
class PreparedAgentRevisionGraph:
    invocations: tuple[PreparedAgentInvocation, ...]
    root_state_policy: RootAgentStatePolicy


class AgentInvocationResolver:
    """Prepare outside I/O, then reauthorize and freeze inside Run acceptance."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        model_selector: AcceptedModelSelector,
        *,
        plugin_runtime_mode: PluginRuntimeMode,
        environment_resolver: AgentEnvironmentSelectionResolver | None = None,
        plugin_resolver: AgentPluginSelectionResolver | None = None,
        protocol_policy: AgentProtocolPolicy | None = None,
    ) -> None:
        self._sessions = sessions
        self._model_selector = model_selector
        self._environment_resolver = environment_resolver
        self._plugin_runtime_mode = plugin_runtime_mode
        self._plugin_resolver = plugin_resolver or AgentPluginSelectionResolver(
            sessions,
            runtime_mode=plugin_runtime_mode,
        )
        self._protocol_policy = protocol_policy or AgentProtocolPolicy()

    async def prepare(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        agent_revision_id: str | None = None,
        expected_current_revision_id: str | None = None,
        config_override: AgentRunOverride | None = None,
        _root_state_policy: RootAgentStatePolicy = RootAgentStatePolicy.invocable,
    ) -> PreparedAgentInvocation:
        workspace_id = actor.boundary_workspace_id
        try:
            async with short_session(self._sessions) as session:
                authorized = await authorize_agent(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    action=WorkspaceAction.agent_invoke,
                )
                agent = await _load_agent(
                    session,
                    organization_id=authorized.organization_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    for_update=False,
                )
                _require_invocable_agent(agent, policy=_root_state_policy)
                if (
                    expected_current_revision_id is not None
                    and agent.current_revision_id != expected_current_revision_id
                ):
                    raise current_revision_conflict(agent.current_revision_id)
                selector_kind = AgentSelectorKind.exact if agent_revision_id is not None else AgentSelectorKind.current
                revision_id = agent_revision_id or agent.current_revision_id
                if revision_id is None:
                    raise agent_current_revision_missing()
                revision_record = await _load_revision(
                    session,
                    organization_id=authorized.organization_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    revision_id=revision_id,
                    for_update=False,
                )
                revision = revision_record.to_resource()
                if revision.plugin_runtime_mode is not self._plugin_runtime_mode:
                    raise agent_revision_not_executable("plugin_runtime_mode_mismatch")
                merged = merge_agent_run_override(revision.config, config_override)
                try:
                    validate_agent_config(merged.config, protocol_policy=self._protocol_policy)
                except AgentConfigValidationError as error:
                    raise agent_revision_not_executable(error.reason) from error
                _require_connectivity_resolution(merged)
                await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.models_read,
                )
                skills = await _prepare_skills(
                    session,
                    actor=actor,
                    organization_id=authorized.organization_id,
                    workspace_id=workspace_id,
                    selections=merged.config.skills,
                    retained=(revision.resolved_skills if merged.config.skills == revision.config.skills else None),
                )
                if merged.config.plugins == revision.config.plugins:
                    try:
                        plugins = await self._plugin_resolver.prepare_retained(
                            session,
                            actor=actor,
                            workspace_id=workspace_id,
                            selections=merged.config.plugins,
                            resolved=revision.resolved_plugin_versions,
                            runtime_lock_digest=revision.runtime_lock_digest,
                        )
                    except PluginSelectionError as error:
                        raise agent_revision_not_executable(error.reason) from error
                else:
                    try:
                        plugins = await self._plugin_resolver.prepare(
                            session,
                            actor=actor,
                            workspace_id=workspace_id,
                            selections=merged.config.plugins,
                        )
                    except PluginSelectionError as error:
                        raise agent_revision_not_executable(error.reason) from error
                resolved_plugins = plugins.resolved
            environment = None
            resolved_environment = None
            if merged.config.environment is not None:
                if self._environment_resolver is None:
                    raise agent_revision_not_executable("environment_resolution_unavailable")
                retained_environment = (
                    revision.resolved_environment if merged.config.environment == revision.config.environment else None
                )
                try:
                    environment = await self._environment_resolver.prepare_invocation(
                        actor=actor,
                        organization_id=authorized.organization_id,
                        workspace_id=workspace_id,
                        selection=merged.config.environment,
                        retained=retained_environment,
                    )
                except EnvironmentManagementError as error:
                    raise agent_revision_not_executable(error.code) from error
                resolved_environment = environment.resolved
            _require_writable_skill_environment(merged.config.skills, resolved_environment)
            async with short_session(self._sessions) as session:
                subagents = await self._prepare_subagents(
                    session,
                    actor=actor,
                    organization_id=authorized.organization_id,
                    workspace_id=workspace_id,
                    root_agent_id=agent_id,
                    revision=revision,
                    config=merged.config,
                    resolved_environment=resolved_environment,
                )
            try:
                model = await self._model_selector.prepare(
                    organization_id=authorized.organization_id,
                    workspace_id=workspace_id,
                    model_id=(
                        revision.resolved_model.model_id
                        if merged.config.model.model_key == revision.config.model.model_key
                        else None
                    ),
                    model_key=(
                        merged.config.model.model_key
                        if merged.config.model.model_key != revision.config.model.model_key
                        else None
                    ),
                    model_api=merged.config.model.model_api,
                )
            except ModelError as error:
                raise agent_revision_not_executable(_model_reason(error)) from error
        except AuthorizationError as error:
            raise _authorization_error(error) from error
        return PreparedAgentInvocation(
            actor=actor,
            organization_id=authorized.organization_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            agent_revision_id=revision.id,
            selector_kind=selector_kind,
            expected_current_revision_id=expected_current_revision_id,
            revision_content_digest=revision.content_digest,
            revision=revision,
            merged=merged,
            model=model,
            plugins=plugins,
            skills=skills,
            resolved_plugin_versions=resolved_plugins,
            environment=environment,
            resolved_environment=resolved_environment,
            subagents=subagents,
        )

    async def prepare_retained_revision_graph(
        self,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        agent_revision_id: str | None = None,
        root_state_policy: RootAgentStatePolicy = RootAgentStatePolicy.invocable,
    ) -> PreparedAgentRevisionGraph:
        """Preflight one retained root Revision and its complete exact child graph."""

        root = await self.prepare(
            actor=actor,
            agent_id=agent_id,
            agent_revision_id=agent_revision_id,
            _root_state_policy=root_state_policy,
        )
        invocations = [root]
        visited = {root.agent_revision_id}
        pending = list(root.subagents)
        while pending:
            edge = pending.pop().edge
            if edge.child_agent_revision_id in visited:
                continue
            child = await self.prepare(
                actor=actor,
                agent_id=edge.child_agent_id,
                agent_revision_id=edge.child_agent_revision_id,
            )
            visited.add(child.agent_revision_id)
            if len(visited) > MAX_SUBAGENT_NODES:
                raise agent_revision_not_executable("subagent_graph_too_large")
            invocations.append(child)
            pending.extend(child.subagents)
        return PreparedAgentRevisionGraph(
            invocations=tuple(invocations),
            root_state_policy=root_state_policy,
        )

    async def freeze_retained_revision_graph(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedAgentRevisionGraph,
    ) -> None:
        """Recheck all retained graph evidence inside the final transaction."""

        for index, invocation in enumerate(prepared.invocations):
            await self.freeze_in_transaction(
                session,
                prepared=invocation,
                _root_state_policy=(prepared.root_state_policy if index == 0 else RootAgentStatePolicy.invocable),
            )

    async def freeze_in_transaction(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedAgentInvocation,
        _root_state_policy: RootAgentStatePolicy = RootAgentStatePolicy.invocable,
    ) -> FrozenAgentInvocation:
        try:
            await authorize_agent(
                session,
                actor=prepared.actor,
                workspace_id=prepared.workspace_id,
                agent_id=prepared.agent_id,
                action=WorkspaceAction.agent_invoke,
            )
            agent = await _load_agent(
                session,
                organization_id=prepared.organization_id,
                workspace_id=prepared.workspace_id,
                agent_id=prepared.agent_id,
                for_update=True,
            )
            _require_invocable_agent(
                agent,
                policy=_root_state_policy,
            )
            if (
                prepared.expected_current_revision_id is not None
                and agent.current_revision_id != prepared.expected_current_revision_id
            ):
                raise current_revision_conflict(agent.current_revision_id)
            if (
                prepared.selector_kind is AgentSelectorKind.current
                and agent.current_revision_id != prepared.agent_revision_id
            ):
                raise current_revision_conflict(agent.current_revision_id)
            revision_record = await _load_revision(
                session,
                organization_id=prepared.organization_id,
                workspace_id=prepared.workspace_id,
                agent_id=prepared.agent_id,
                revision_id=prepared.agent_revision_id,
                for_update=True,
            )
            if (
                revision_record.content_digest != prepared.revision_content_digest
                or revision_record.plugin_runtime_mode != self._plugin_runtime_mode.value
            ):
                raise agent_revision_not_executable("revision_changed")
            await authorize_workspace(
                session,
                actor=prepared.actor,
                workspace_id=prepared.workspace_id,
                action=WorkspaceAction.models_read,
            )
            try:
                execution = await self._model_selector.freeze_in_transaction(session, prepared=prepared.model)
            except ModelError as error:
                raise agent_revision_not_executable(_model_reason(error)) from error
            skills = await _freeze_skills(session, prepared)
            try:
                environment = (
                    await self._environment_resolver.freeze_in_transaction(
                        session,
                        prepared=prepared.environment,
                    )
                    if self._environment_resolver is not None and prepared.environment is not None
                    else None
                )
            except EnvironmentManagementError as error:
                raise agent_revision_not_executable(error.code) from error
            _require_writable_skill_environment(skills, environment)
            try:
                plugins = (
                    await self._plugin_resolver.freeze_in_transaction(
                        session,
                        actor=prepared.actor,
                        workspace_id=prepared.workspace_id,
                        prepared=prepared.plugins,
                    )
                    if prepared.plugins is not None
                    else prepared.resolved_plugin_versions
                )
            except PluginSelectionError as error:
                raise agent_revision_not_executable(error.reason) from error
            subagents = await _freeze_subagents(session, prepared)
        except AuthorizationError as error:
            raise _authorization_error(error) from error

        resolved_subagents = tuple(item.edge for item in subagents)
        try:
            if _runtime_selection_unchanged(prepared, plugins, resolved_subagents):
                runtime_lock = await self._plugin_resolver.runtime_locks.require(
                    session,
                    prepared.revision.runtime_lock_digest,
                    mode=prepared.revision.plugin_runtime_mode.value,
                )
            else:
                if prepared.plugins is None:
                    raise PluginRuntimeLockError("plugin_runtime_lock_unavailable")
                runtime_lock = await self._plugin_resolver.freeze_runtime_lock(
                    session,
                    prepared=prepared.plugins,
                    child_lock_digests=tuple(item.child_runtime_lock_digest for item in subagents),
                    use_active_catalog=True,
                )
        except PluginRuntimeLockError as error:
            raise agent_revision_not_executable(error.reason) from error
        config_payload = {
            "schema_version": "1",
            "resolved_model": EffectiveAgentModel(
                execution=execution,
                settings=prepared.merged.config.model.settings,
                characteristics=prepared.merged.config.model.characteristics,
            ),
            "resolved_plugin_versions": plugins,
            "runtime_lock_digest": runtime_lock.digest,
            "skills": skills,
            "connector_tools": tuple(prepared.merged.config.connector_tools.values()),
            "mcp_tools": tuple(prepared.merged.config.mcp_tools.values()),
            "resolved_environment": environment,
            "resolved_subagents": resolved_subagents,
            "instructions": prepared.merged.config.instructions,
            "input_adapter": prepared.merged.config.input_adapter,
            "client_tools": prepared.merged.config.client_tools,
            "output_spec": prepared.merged.config.output_spec,
            "retries": prepared.merged.config.retries,
            "secret_requirements": prepared.merged.config.secret_requirements,
            "asset_publication": prepared.merged.config.asset_publication,
            "protocol": prepared.merged.config.protocol,
        }
        effective_without_digest = EffectiveAgentConfig(
            **config_payload,
            content_digest="0" * 64,
        )
        digest_payload = effective_without_digest.model_dump(
            mode="json",
            by_alias=True,
            exclude={"content_digest"},
        )
        effective = EffectiveAgentConfig(
            **config_payload,
            content_digest=canonical_digest(digest_payload),
        )
        return FrozenAgentInvocation(
            agent_id=prepared.agent_id,
            agent_revision_id=prepared.agent_revision_id,
            selector_kind=prepared.selector_kind,
            effective_config=effective,
            sensitive_values=prepared.merged.sensitive_values,
            sensitive_values_digest=canonical_digest(prepared.merged.sensitive_values),
        )

    async def _prepare_subagents(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        root_agent_id: str,
        revision: AgentRevision,
        config,
        resolved_environment: EnvironmentExecutionConfig | None,
    ) -> tuple[PreparedInvocationSubagent, ...]:
        base_edges = {item.name: item for item in revision.resolved_subagents}
        result: list[PreparedInvocationSubagent] = []
        for name, selection in config.subagents.items():
            base_selection = revision.config.subagents.get(name)
            base_edge = base_edges.get(name)
            exact_revision_id = (
                base_edge.child_agent_revision_id if base_selection == selection and base_edge is not None else None
            )
            await authorize_agent(
                session,
                actor=actor,
                workspace_id=workspace_id,
                agent_id=selection.agent_id,
                action=WorkspaceAction.agent_invoke,
            )
            child = await _load_agent(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                agent_id=selection.agent_id,
                for_update=False,
            )
            _require_invocable_agent(child)
            if exact_revision_id is None:
                exact_revision_id = await _select_child_revision_id(
                    session,
                    child=child,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    version=selection.version,
                )
            child_revision = await _load_revision(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                agent_id=child.id,
                revision_id=exact_revision_id,
                for_update=False,
            )
            if child_revision.plugin_runtime_mode != self._plugin_runtime_mode.value:
                raise agent_revision_not_executable("subagent_runtime_mode_mismatch")
            await _validate_subagent_graph(
                session,
                root_agent_id=root_agent_id,
                first_revision=child_revision,
            )
            _validate_child_environment(
                resolved_environment,
                child_revision.to_resource().resolved_environment,
                selection,
            )
            result.append(
                PreparedInvocationSubagent(
                    edge=ResolvedSubagentEdge(
                        name=name,
                        child_agent_id=selection.agent_id,
                        child_agent_revision_id=child_revision.id,
                        description=selection.description,
                        context=selection.context,
                        usage_limits=selection.usage_limits,
                        environment=selection.environment,
                    ),
                    child_revision_digest=child_revision.content_digest,
                    child_runtime_lock_digest=child_revision.runtime_lock_digest,
                )
            )
        return tuple(result)


def _require_writable_skill_environment(
    skills: Sequence[object],
    environment: EnvironmentExecutionConfig | None,
) -> None:
    if not skills:
        return
    if environment is None:
        raise agent_revision_not_executable("skill_environment_required")
    if environment.access == "read_only":
        raise agent_revision_not_executable("skill_environment_not_writable")


def _require_connectivity_resolution(merged: MergedAgentRun) -> None:
    if merged.config.connector_tools:
        raise agent_revision_not_executable("connector_tool_resolution_unavailable")
    if merged.config.mcp_tools:
        raise agent_revision_not_executable("mcp_tool_resolution_unavailable")


async def _prepare_skills(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    selections: tuple[SkillSelection, ...],
    retained: tuple[ResolvedSkillBinding, ...] | None,
) -> tuple[PreparedSkillLock, ...]:
    if not selections:
        return ()
    await authorize_workspace(
        session,
        actor=actor,
        workspace_id=workspace_id,
        action=WorkspaceAction.skill_read,
    )
    try:
        if retained is not None:
            if tuple((item.skill_key, item.version) for item in retained) != tuple(
                (item.skill_key, item.version) for item in selections
            ):
                raise SkillSelectionInvalid
            return await prepare_skill_locks_from_bindings(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                bindings=retained,
            )
        return await prepare_skill_locks_from_selections(
            session,
            organization_id=organization_id,
            workspace_id=workspace_id,
            selections=selections,
        )
    except SkillSelectionInvalid as error:
        raise agent_revision_not_executable("skill_selection_invalid") from error


async def _freeze_skills(
    session: AsyncSession,
    prepared: PreparedAgentInvocation,
) -> tuple[SkillRevisionLock, ...]:
    if not prepared.skills:
        return ()
    await authorize_workspace(
        session,
        actor=prepared.actor,
        workspace_id=prepared.workspace_id,
        action=WorkspaceAction.skill_read,
    )
    try:
        return await freeze_skill_locks(
            session,
            organization_id=prepared.organization_id,
            workspace_id=prepared.workspace_id,
            prepared=prepared.skills,
        )
    except SkillSelectionInvalid as error:
        raise agent_revision_not_executable("skill_selection_invalid") from error


async def _freeze_subagents(
    session: AsyncSession,
    prepared: PreparedAgentInvocation,
) -> tuple[PreparedInvocationSubagent, ...]:
    result: list[PreparedInvocationSubagent] = []
    for expected in prepared.subagents:
        await authorize_agent(
            session,
            actor=prepared.actor,
            workspace_id=prepared.workspace_id,
            agent_id=expected.edge.child_agent_id,
            action=WorkspaceAction.agent_invoke,
        )
        child = await _load_agent(
            session,
            organization_id=prepared.organization_id,
            workspace_id=prepared.workspace_id,
            agent_id=expected.edge.child_agent_id,
            for_update=True,
        )
        _require_invocable_agent(child)
        revision = await _load_revision(
            session,
            organization_id=prepared.organization_id,
            workspace_id=prepared.workspace_id,
            agent_id=child.id,
            revision_id=expected.edge.child_agent_revision_id,
            for_update=True,
        )
        if (
            revision.content_digest != expected.child_revision_digest
            or revision.runtime_lock_digest != expected.child_runtime_lock_digest
        ):
            raise agent_revision_not_executable("subagent_revision_changed")
        result.append(expected)
    return tuple(result)


async def _load_agent(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    agent_id: str,
    for_update: bool,
) -> AgentRecord:
    statement = select(AgentRecord).where(
        AgentRecord.id == agent_id,
        AgentRecord.organization_id == organization_id,
        AgentRecord.workspace_id == workspace_id,
    )
    if for_update:
        statement = statement.with_for_update()
    record = await session.scalar(statement)
    if record is None:
        raise agent_not_found()
    return record


async def _load_revision(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    agent_id: str,
    revision_id: str,
    for_update: bool,
) -> AgentRevisionRecord:
    statement = select(AgentRevisionRecord).where(
        AgentRevisionRecord.id == revision_id,
        AgentRevisionRecord.agent_id == agent_id,
        AgentRevisionRecord.organization_id == organization_id,
        AgentRevisionRecord.workspace_id == workspace_id,
    )
    if for_update:
        statement = statement.with_for_update()
    record = await session.scalar(statement)
    if record is None:
        raise agent_revision_not_found()
    return record


def _require_invocable_agent(
    agent: AgentRecord,
    *,
    policy: RootAgentStatePolicy = RootAgentStatePolicy.invocable,
) -> None:
    if not agent.enabled and policy is RootAgentStatePolicy.invocable:
        raise agent_disabled()
    if agent.archived_at is not None and policy is not RootAgentStatePolicy.archived_allowed:
        raise agent_archived()


async def _select_child_revision_id(
    session: AsyncSession,
    *,
    child: AgentRecord,
    organization_id: str,
    workspace_id: str,
    version: int | None,
) -> str:
    if version is None:
        return child.current_revision_id
    revision_id = await session.scalar(
        select(AgentRevisionRecord.id).where(
            AgentRevisionRecord.agent_id == child.id,
            AgentRevisionRecord.organization_id == organization_id,
            AgentRevisionRecord.workspace_id == workspace_id,
            AgentRevisionRecord.version == version,
        )
    )
    if revision_id is None:
        raise agent_revision_not_executable("subagent_revision_unavailable")
    return revision_id


async def _validate_subagent_graph(
    session: AsyncSession,
    *,
    root_agent_id: str,
    first_revision: AgentRevisionRecord,
) -> None:
    pending: list[tuple[AgentRevisionRecord, int]] = [(first_revision, 1)]
    visited: set[str] = set()
    while pending:
        revision, depth = pending.pop()
        if depth > MAX_SUBAGENT_DEPTH:
            raise agent_revision_not_executable("subagent_graph_too_deep")
        if revision.agent_id == root_agent_id:
            raise agent_revision_not_executable("subagent_cycle")
        if revision.id in visited:
            continue
        visited.add(revision.id)
        if len(visited) > MAX_SUBAGENT_NODES:
            raise agent_revision_not_executable("subagent_graph_too_large")
        child_ids = tuple(item["child_agent_revision_id"] for item in revision.resolved_subagents)
        if not child_ids:
            continue
        children = tuple(
            (await session.scalars(select(AgentRevisionRecord).where(AgentRevisionRecord.id.in_(child_ids)))).all()
        )
        if len(children) != len(set(child_ids)):
            raise agent_revision_not_executable("subagent_revision_unavailable")
        pending.extend((child, depth + 1) for child in children)


def _validate_child_environment(
    root_environment: EnvironmentExecutionConfig | None,
    child_environment: EnvironmentExecutionConfig | None,
    selection: SubagentSelection,
) -> None:
    mode = selection.environment.mode
    if mode == "none" and child_environment is not None:
        raise agent_revision_not_executable("subagent_environment_required")
    if mode == "dedicated" and child_environment is None:
        raise agent_revision_not_executable("subagent_environment_required")
    if mode == "shared_root":
        if (
            root_environment is None
            or child_environment is None
            or _environment_target_identity(root_environment) != _environment_target_identity(child_environment)
            or _access_rank(child_environment.access) > _access_rank(root_environment.access)
        ):
            raise agent_revision_not_executable("subagent_environment_incompatible")


def _environment_target_identity(environment: EnvironmentExecutionConfig) -> tuple[object, ...]:
    return (
        environment.connection,
        environment.provider_package_revision_id,
        environment.provider_lock,
        environment.target_key,
    )


def _access_rank(access: str) -> int:
    return {"read_only": 0, "read_write": 1, "full": 2}[access]


def _runtime_selection_unchanged(
    prepared: PreparedAgentInvocation,
    resolved_plugins: tuple[ResolvedPluginVersion, ...],
    resolved_subagents: tuple[ResolvedSubagentEdge, ...],
) -> bool:
    return _plugin_runtime_signature(resolved_plugins) == _plugin_runtime_signature(
        prepared.revision.resolved_plugin_versions
    ) and _subagent_runtime_signature(resolved_subagents) == _subagent_runtime_signature(
        prepared.revision.resolved_subagents
    )


def _plugin_runtime_signature(plugins: tuple[ResolvedPluginVersion, ...]) -> frozenset[tuple[str, ...]]:
    return frozenset(
        (
            item.plugin_id,
            item.plugin_version_id,
            item.plugin_key,
            item.distribution_name,
            item.version,
            item.top_level_package,
            item.wheel_digest,
        )
        for item in plugins
    )


def _subagent_runtime_signature(subagents: tuple[ResolvedSubagentEdge, ...]) -> frozenset[str]:
    return frozenset(item.child_agent_revision_id for item in subagents)


def _authorization_error(error: AuthorizationError) -> AgentError:
    if error.concealed:
        return agent_not_found()
    return AgentError("forbidden", "The operation is not allowed.", status_code=403)


def _model_reason(error: ModelError) -> str:
    return {
        "model_not_found": "model_unavailable",
        "model_disabled": "model_unavailable",
        "credential_not_eligible": "model_credential_unavailable",
        "model_configuration_changed": "model_configuration_changed",
        "invalid_model_configuration": "model_incompatible",
    }.get(error.code, "model_unavailable")
