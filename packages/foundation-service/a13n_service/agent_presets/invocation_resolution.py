"""Two-phase AgentPreset invocation selection and immutable config freezing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_agent_preset,
    authorize_workspace,
)
from a13n_service.model_configs.runtime import (
    AcceptedModelSelector,
    PreparedModelExecution,
    PreparedModelSnapshotExecution,
)
from a13n_service.model_configs.service import ModelConfigError
from a13n_service.plugins.runtime import PluginRuntimeLockError
from a13n_service.skills.domain import SkillPackageManifest
from a13n_service.skills.models import SkillRecord, SkillRevisionRecord
from a13n_service.storage import short_session

from .domain import (
    AgentPresetLifecycleState,
    AgentPresetRevision,
    AgentRunOverride,
    EffectiveAgentConfig,
    EnvironmentExecutionConfig,
    PluginRuntimeMode,
    ResolvedAgentModelConfig,
    ResolvedPluginVersion,
    ResolvedSkillSelection,
    ResolvedSubagentEdge,
    SkillSelection,
    SubagentSelection,
    canonical_digest,
)
from .environment_resolution import AgentEnvironmentSelectionResolver, PreparedEnvironmentSelection
from .errors import (
    AgentPresetError,
    default_revision_conflict,
    preset_archived,
    preset_default_revision_missing,
    preset_disabled,
    preset_not_found,
    preset_revision_not_executable,
    preset_revision_not_found,
)
from .invocation import AgentRunSensitiveValues, MergedAgentRun, merge_agent_run_override
from .models import AgentPresetRecord, AgentPresetRevisionRecord
from .plugin_resolution import AgentPluginSelectionResolver, PluginSelectionError, PreparedPluginSelections
from .resolution import MAX_SUBAGENT_DEPTH, MAX_SUBAGENT_NODES
from .validation import AgentConfigValidationError, AgentProtocolPolicy, validate_agent_config


class AgentPresetSelectorKind(StrEnum):
    default = "default"
    exact = "exact"


@dataclass(frozen=True, slots=True)
class PreparedInvocationSkill:
    revision_id: str
    skill_id: str
    skill_name: str
    content_digest: str


@dataclass(frozen=True, slots=True)
class PreparedInvocationSubagent:
    edge: ResolvedSubagentEdge
    child_revision_digest: str
    child_runtime_lock_digest: str


PreparedInvocationModel = PreparedModelExecution | PreparedModelSnapshotExecution


@dataclass(frozen=True, slots=True)
class PreparedAgentInvocation:
    actor: AuthenticatedActor
    organization_id: str
    workspace_id: str
    agent_preset_id: str
    agent_preset_revision_id: str
    selector_kind: AgentPresetSelectorKind
    expected_default_revision_id: str | None
    revision_content_digest: str
    revision: AgentPresetRevision
    merged: MergedAgentRun
    model: PreparedInvocationModel
    plugins: PreparedPluginSelections | None
    skills: tuple[PreparedInvocationSkill, ...]
    resolved_plugin_versions: tuple[ResolvedPluginVersion, ...]
    environment: PreparedEnvironmentSelection | None
    resolved_environment: EnvironmentExecutionConfig | None
    subagents: tuple[PreparedInvocationSubagent, ...]


@dataclass(frozen=True, slots=True)
class FrozenAgentInvocation:
    agent_preset_id: str
    agent_preset_revision_id: str
    selector_kind: AgentPresetSelectorKind
    effective_config: EffectiveAgentConfig
    sensitive_values: AgentRunSensitiveValues
    sensitive_values_digest: str


@dataclass(frozen=True, slots=True)
class PreparedAgentPresetRevisionGraph:
    invocations: tuple[PreparedAgentInvocation, ...]
    allow_disabled_root: bool


class AgentPresetInvocationResolver:
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
        agent_preset_id: str,
        agent_preset_revision_id: str | None = None,
        expected_default_revision_id: str | None = None,
        config_override: AgentRunOverride | None = None,
        _allow_disabled_root: bool = False,
    ) -> PreparedAgentInvocation:
        workspace_id = actor.boundary_workspace_id
        try:
            async with short_session(self._sessions) as session:
                authorized = await authorize_agent_preset(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    agent_preset_id=agent_preset_id,
                    action=WorkspaceAction.agent_preset_invoke,
                )
                preset = await _load_preset(
                    session,
                    organization_id=authorized.organization_id,
                    workspace_id=workspace_id,
                    preset_id=agent_preset_id,
                    for_update=False,
                )
                _require_invocable_preset(preset, allow_disabled=_allow_disabled_root)
                if (
                    expected_default_revision_id is not None
                    and preset.default_revision_id != expected_default_revision_id
                ):
                    raise default_revision_conflict(preset.default_revision_id)
                selector_kind = (
                    AgentPresetSelectorKind.exact
                    if agent_preset_revision_id is not None
                    else AgentPresetSelectorKind.default
                )
                revision_id = agent_preset_revision_id or preset.default_revision_id
                if revision_id is None:
                    raise preset_default_revision_missing()
                revision_record = await _load_revision(
                    session,
                    organization_id=authorized.organization_id,
                    workspace_id=workspace_id,
                    preset_id=agent_preset_id,
                    revision_id=revision_id,
                    for_update=False,
                )
                revision = revision_record.to_resource()
                if revision.plugin_runtime_mode is not self._plugin_runtime_mode:
                    raise preset_revision_not_executable("plugin_runtime_mode_mismatch")
                merged = merge_agent_run_override(revision.config, config_override)
                try:
                    validate_agent_config(merged.config, protocol_policy=self._protocol_policy)
                except AgentConfigValidationError as error:
                    raise preset_revision_not_executable(error.reason) from error
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
                        raise preset_revision_not_executable(error.reason) from error
                else:
                    try:
                        plugins = await self._plugin_resolver.prepare(
                            session,
                            actor=actor,
                            workspace_id=workspace_id,
                            selections=merged.config.plugins,
                        )
                    except PluginSelectionError as error:
                        raise preset_revision_not_executable(error.reason) from error
                resolved_plugins = plugins.resolved
            environment = None
            resolved_environment = None
            if merged.config.environment is not None:
                if self._environment_resolver is None:
                    raise preset_revision_not_executable("environment_resolution_unavailable")
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
                    raise preset_revision_not_executable(error.code) from error
                resolved_environment = environment.resolved
            async with short_session(self._sessions) as session:
                subagents = await self._prepare_subagents(
                    session,
                    actor=actor,
                    organization_id=authorized.organization_id,
                    workspace_id=workspace_id,
                    root_preset_id=agent_preset_id,
                    revision=revision,
                    config=merged.config,
                    resolved_environment=resolved_environment,
                )
            try:
                if merged.config.model.model_config_id == revision.config.model.model_config_id:
                    model: PreparedInvocationModel = await self._model_selector.prepare_snapshot(
                        organization_id=authorized.organization_id,
                        workspace_id=workspace_id,
                        snapshot=revision.resolved_model.execution,
                        invoking_principal=actor.principal,
                    )
                else:
                    model = await self._model_selector.prepare(
                        organization_id=authorized.organization_id,
                        workspace_id=workspace_id,
                        model_id=merged.config.model.model_config_id,
                        invoking_principal=actor.principal,
                    )
            except ModelConfigError as error:
                raise preset_revision_not_executable(_model_reason(error)) from error
        except AuthorizationError as error:
            raise _authorization_error(error) from error
        return PreparedAgentInvocation(
            actor=actor,
            organization_id=authorized.organization_id,
            workspace_id=workspace_id,
            agent_preset_id=agent_preset_id,
            agent_preset_revision_id=revision.id,
            selector_kind=selector_kind,
            expected_default_revision_id=expected_default_revision_id,
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
        agent_preset_id: str,
        agent_preset_revision_id: str | None = None,
        allow_disabled_root: bool = False,
    ) -> PreparedAgentPresetRevisionGraph:
        """Preflight one retained root Revision and its complete exact child graph."""

        root = await self.prepare(
            actor=actor,
            agent_preset_id=agent_preset_id,
            agent_preset_revision_id=agent_preset_revision_id,
            _allow_disabled_root=allow_disabled_root,
        )
        invocations = [root]
        visited = {root.agent_preset_revision_id}
        pending = list(root.subagents)
        while pending:
            edge = pending.pop().edge
            if edge.child_agent_preset_revision_id in visited:
                continue
            child = await self.prepare(
                actor=actor,
                agent_preset_id=edge.child_agent_preset_id,
                agent_preset_revision_id=edge.child_agent_preset_revision_id,
            )
            visited.add(child.agent_preset_revision_id)
            if len(visited) > MAX_SUBAGENT_NODES:
                raise preset_revision_not_executable("subagent_graph_too_large")
            invocations.append(child)
            pending.extend(child.subagents)
        return PreparedAgentPresetRevisionGraph(
            invocations=tuple(invocations),
            allow_disabled_root=allow_disabled_root,
        )

    async def freeze_retained_revision_graph(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedAgentPresetRevisionGraph,
    ) -> None:
        """Recheck all retained graph evidence inside the final transaction."""

        for index, invocation in enumerate(prepared.invocations):
            await self.freeze_in_transaction(
                session,
                prepared=invocation,
                _allow_disabled_root=index == 0 and prepared.allow_disabled_root,
            )

    async def freeze_in_transaction(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedAgentInvocation,
        _allow_disabled_root: bool = False,
    ) -> FrozenAgentInvocation:
        try:
            await authorize_agent_preset(
                session,
                actor=prepared.actor,
                workspace_id=prepared.workspace_id,
                agent_preset_id=prepared.agent_preset_id,
                action=WorkspaceAction.agent_preset_invoke,
            )
            preset = await _load_preset(
                session,
                organization_id=prepared.organization_id,
                workspace_id=prepared.workspace_id,
                preset_id=prepared.agent_preset_id,
                for_update=True,
            )
            _require_invocable_preset(preset, allow_disabled=_allow_disabled_root)
            if (
                prepared.expected_default_revision_id is not None
                and preset.default_revision_id != prepared.expected_default_revision_id
            ):
                raise default_revision_conflict(preset.default_revision_id)
            if (
                prepared.selector_kind is AgentPresetSelectorKind.default
                and preset.default_revision_id != prepared.agent_preset_revision_id
            ):
                raise default_revision_conflict(preset.default_revision_id)
            revision_record = await _load_revision(
                session,
                organization_id=prepared.organization_id,
                workspace_id=prepared.workspace_id,
                preset_id=prepared.agent_preset_id,
                revision_id=prepared.agent_preset_revision_id,
                for_update=True,
            )
            if (
                revision_record.content_digest != prepared.revision_content_digest
                or revision_record.plugin_runtime_mode != self._plugin_runtime_mode.value
            ):
                raise preset_revision_not_executable("revision_changed")
            await authorize_workspace(
                session,
                actor=prepared.actor,
                workspace_id=prepared.workspace_id,
                action=WorkspaceAction.models_read,
            )
            try:
                if isinstance(prepared.model, PreparedModelSnapshotExecution):
                    execution = await self._model_selector.freeze_snapshot_in_transaction(
                        session,
                        prepared=prepared.model,
                    )
                else:
                    execution = await self._model_selector.freeze_in_transaction(session, prepared=prepared.model)
            except ModelConfigError as error:
                raise preset_revision_not_executable(_model_reason(error)) from error
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
                raise preset_revision_not_executable(error.code) from error
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
                raise preset_revision_not_executable(error.reason) from error
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
            raise preset_revision_not_executable(error.reason) from error
        config_payload = {
            "schema_version": "1",
            "resolved_model": ResolvedAgentModelConfig(
                execution=execution,
                settings=prepared.merged.config.model.settings,
                characteristics=prepared.merged.config.model.characteristics,
            ),
            "resolved_plugin_versions": plugins,
            "runtime_lock_digest": runtime_lock.digest,
            "resolved_skills": skills,
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
            agent_preset_id=prepared.agent_preset_id,
            agent_preset_revision_id=prepared.agent_preset_revision_id,
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
        root_preset_id: str,
        revision: AgentPresetRevision,
        config,
        resolved_environment: EnvironmentExecutionConfig | None,
    ) -> tuple[PreparedInvocationSubagent, ...]:
        base_edges = {item.name: item for item in revision.resolved_subagents}
        result: list[PreparedInvocationSubagent] = []
        for name, selection in config.subagents.items():
            base_selection = revision.config.subagents.get(name)
            base_edge = base_edges.get(name)
            exact_revision_id = (
                base_edge.child_agent_preset_revision_id
                if base_selection == selection and base_edge is not None
                else None
            )
            await authorize_agent_preset(
                session,
                actor=actor,
                workspace_id=workspace_id,
                agent_preset_id=selection.agent_preset_id,
                action=WorkspaceAction.agent_preset_invoke,
            )
            child = await _load_preset(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                preset_id=selection.agent_preset_id,
                for_update=False,
            )
            _require_invocable_preset(child)
            if exact_revision_id is None:
                exact_revision_id = await _select_child_revision_id(
                    session,
                    child=child,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    revision_number=selection.revision,
                )
            child_revision = await _load_revision(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                preset_id=child.id,
                revision_id=exact_revision_id,
                for_update=False,
            )
            if child_revision.plugin_runtime_mode != self._plugin_runtime_mode.value:
                raise preset_revision_not_executable("subagent_runtime_mode_mismatch")
            await _validate_subagent_graph(
                session,
                root_preset_id=root_preset_id,
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
                        child_agent_preset_id=selection.agent_preset_id,
                        child_agent_preset_revision_id=child_revision.id,
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


async def _prepare_skills(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    selections: tuple[SkillSelection, ...],
) -> tuple[PreparedInvocationSkill, ...]:
    if not selections:
        return ()
    await authorize_workspace(
        session,
        actor=actor,
        workspace_id=workspace_id,
        action=WorkspaceAction.skill_read,
    )
    ids = tuple(item.skill_revision_id for item in selections)
    rows = tuple(
        (
            await session.execute(
                select(SkillRevisionRecord, SkillRecord)
                .join(SkillRecord, SkillRecord.id == SkillRevisionRecord.skill_id)
                .where(
                    SkillRevisionRecord.organization_id == organization_id,
                    SkillRevisionRecord.workspace_id == workspace_id,
                    SkillRevisionRecord.id.in_(ids),
                    SkillRecord.deleted_at.is_(None),
                )
            )
        ).all()
    )
    by_id = {revision.id: (revision, skill) for revision, skill in rows}
    if set(by_id) != set(ids):
        raise preset_revision_not_executable("skill_revision_unavailable")
    result: list[PreparedInvocationSkill] = []
    names: set[str] = set()
    for revision_id in ids:
        skill_revision, _skill = by_id[revision_id]
        manifest = SkillPackageManifest.model_validate(skill_revision.manifest)
        if manifest.skill_name in names or manifest.content_digest != skill_revision.content_digest:
            raise preset_revision_not_executable("skill_revision_invalid")
        names.add(manifest.skill_name)
        result.append(
            PreparedInvocationSkill(
                revision_id=revision_id,
                skill_id=skill_revision.skill_id,
                skill_name=manifest.skill_name,
                content_digest=skill_revision.content_digest,
            )
        )
    return tuple(result)


async def _freeze_skills(
    session: AsyncSession,
    prepared: PreparedAgentInvocation,
) -> tuple[ResolvedSkillSelection, ...]:
    if not prepared.skills:
        return ()
    await authorize_workspace(
        session,
        actor=prepared.actor,
        workspace_id=prepared.workspace_id,
        action=WorkspaceAction.skill_read,
    )
    ids = tuple(item.revision_id for item in prepared.skills)
    rows = tuple(
        (
            await session.scalars(
                select(SkillRevisionRecord)
                .where(
                    SkillRevisionRecord.organization_id == prepared.organization_id,
                    SkillRevisionRecord.workspace_id == prepared.workspace_id,
                    SkillRevisionRecord.id.in_(ids),
                )
                .with_for_update()
            )
        ).all()
    )
    current = {item.id: item for item in rows}
    result: list[ResolvedSkillSelection] = []
    for expected in prepared.skills:
        row = current.get(expected.revision_id)
        if row is None or row.skill_id != expected.skill_id or row.content_digest != expected.content_digest:
            raise preset_revision_not_executable("skill_revision_changed")
        result.append(
            ResolvedSkillSelection(
                skill_revision_id=expected.revision_id,
                skill_name=expected.skill_name,
                content_digest=expected.content_digest,
            )
        )
    return tuple(result)


async def _freeze_subagents(
    session: AsyncSession,
    prepared: PreparedAgentInvocation,
) -> tuple[PreparedInvocationSubagent, ...]:
    result: list[PreparedInvocationSubagent] = []
    for expected in prepared.subagents:
        await authorize_agent_preset(
            session,
            actor=prepared.actor,
            workspace_id=prepared.workspace_id,
            agent_preset_id=expected.edge.child_agent_preset_id,
            action=WorkspaceAction.agent_preset_invoke,
        )
        child = await _load_preset(
            session,
            organization_id=prepared.organization_id,
            workspace_id=prepared.workspace_id,
            preset_id=expected.edge.child_agent_preset_id,
            for_update=True,
        )
        _require_invocable_preset(child)
        revision = await _load_revision(
            session,
            organization_id=prepared.organization_id,
            workspace_id=prepared.workspace_id,
            preset_id=child.id,
            revision_id=expected.edge.child_agent_preset_revision_id,
            for_update=True,
        )
        if (
            revision.content_digest != expected.child_revision_digest
            or revision.runtime_lock_digest != expected.child_runtime_lock_digest
        ):
            raise preset_revision_not_executable("subagent_revision_changed")
        result.append(expected)
    return tuple(result)


async def _load_preset(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    preset_id: str,
    for_update: bool,
) -> AgentPresetRecord:
    statement = select(AgentPresetRecord).where(
        AgentPresetRecord.id == preset_id,
        AgentPresetRecord.organization_id == organization_id,
        AgentPresetRecord.workspace_id == workspace_id,
    )
    if for_update:
        statement = statement.with_for_update()
    record = await session.scalar(statement)
    if record is None:
        raise preset_not_found()
    return record


async def _load_revision(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    preset_id: str,
    revision_id: str,
    for_update: bool,
) -> AgentPresetRevisionRecord:
    statement = select(AgentPresetRevisionRecord).where(
        AgentPresetRevisionRecord.id == revision_id,
        AgentPresetRevisionRecord.agent_preset_id == preset_id,
        AgentPresetRevisionRecord.organization_id == organization_id,
        AgentPresetRevisionRecord.workspace_id == workspace_id,
    )
    if for_update:
        statement = statement.with_for_update()
    record = await session.scalar(statement)
    if record is None:
        raise preset_revision_not_found()
    return record


def _require_invocable_preset(preset: AgentPresetRecord, *, allow_disabled: bool = False) -> None:
    state = AgentPresetLifecycleState(preset.lifecycle_state)
    if state is AgentPresetLifecycleState.disabled and not allow_disabled:
        raise preset_disabled()
    if state is AgentPresetLifecycleState.archived:
        raise preset_archived()


async def _select_child_revision_id(
    session: AsyncSession,
    *,
    child: AgentPresetRecord,
    organization_id: str,
    workspace_id: str,
    revision_number: int | None,
) -> str:
    if revision_number is None:
        if child.default_revision_id is None:
            raise preset_revision_not_executable("subagent_default_revision_missing")
        return child.default_revision_id
    revision_id = await session.scalar(
        select(AgentPresetRevisionRecord.id).where(
            AgentPresetRevisionRecord.agent_preset_id == child.id,
            AgentPresetRevisionRecord.organization_id == organization_id,
            AgentPresetRevisionRecord.workspace_id == workspace_id,
            AgentPresetRevisionRecord.revision_number == revision_number,
        )
    )
    if revision_id is None:
        raise preset_revision_not_executable("subagent_revision_unavailable")
    return revision_id


async def _validate_subagent_graph(
    session: AsyncSession,
    *,
    root_preset_id: str,
    first_revision: AgentPresetRevisionRecord,
) -> None:
    pending: list[tuple[AgentPresetRevisionRecord, int]] = [(first_revision, 1)]
    visited: set[str] = set()
    while pending:
        revision, depth = pending.pop()
        if depth > MAX_SUBAGENT_DEPTH:
            raise preset_revision_not_executable("subagent_graph_too_deep")
        if revision.agent_preset_id == root_preset_id:
            raise preset_revision_not_executable("subagent_cycle")
        if revision.id in visited:
            continue
        visited.add(revision.id)
        if len(visited) > MAX_SUBAGENT_NODES:
            raise preset_revision_not_executable("subagent_graph_too_large")
        child_ids = tuple(item["child_agent_preset_revision_id"] for item in revision.resolved_subagents)
        if not child_ids:
            continue
        children = tuple(
            (
                await session.scalars(
                    select(AgentPresetRevisionRecord).where(AgentPresetRevisionRecord.id.in_(child_ids))
                )
            ).all()
        )
        if len(children) != len(set(child_ids)):
            raise preset_revision_not_executable("subagent_revision_unavailable")
        pending.extend((child, depth + 1) for child in children)


def _validate_child_environment(
    root_environment: EnvironmentExecutionConfig | None,
    child_environment: EnvironmentExecutionConfig | None,
    selection: SubagentSelection,
) -> None:
    mode = selection.environment.mode
    if mode == "none" and child_environment is not None:
        raise preset_revision_not_executable("subagent_environment_required")
    if mode == "dedicated" and child_environment is None:
        raise preset_revision_not_executable("subagent_environment_required")
    if mode == "shared_root":
        if (
            root_environment is None
            or child_environment is None
            or root_environment.logical_digest_sha256 != child_environment.logical_digest_sha256
            or _access_rank(child_environment.access) > _access_rank(root_environment.access)
        ):
            raise preset_revision_not_executable("subagent_environment_incompatible")


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
            item.distribution_version,
            item.top_level_package,
            item.wheel_digest,
        )
        for item in plugins
    )


def _subagent_runtime_signature(subagents: tuple[ResolvedSubagentEdge, ...]) -> frozenset[str]:
    return frozenset(item.child_agent_preset_revision_id for item in subagents)


def _authorization_error(error: AuthorizationError) -> AgentPresetError:
    if error.concealed:
        return preset_not_found()
    return AgentPresetError("forbidden", "The operation is not allowed.", status_code=403)


def _model_reason(error: ModelConfigError) -> str:
    return {
        "model_not_found": "model_unavailable",
        "model_disabled": "model_unavailable",
        "credential_not_eligible": "model_credential_unavailable",
        "model_configuration_changed": "model_configuration_changed",
        "invalid_model_configuration": "model_incompatible",
    }.get(error.code, "model_unavailable")
