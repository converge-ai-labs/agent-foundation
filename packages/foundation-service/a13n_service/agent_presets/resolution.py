"""Two-phase AgentPreset publication resolution."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.iam import AuthenticatedActor, authorize_agent_preset, authorize_agent_skill_binding
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.model_configs.runtime import AcceptedModelSelector, PreparedModelExecution
from a13n_service.skills.domain import SkillPackageManifest
from a13n_service.skills.models import SkillRecord, SkillRevisionRecord
from a13n_service.storage import short_session

from .domain import (
    AgentPresetConfig,
    ChildEnvironmentPolicy,
    EnvironmentExecutionConfig,
    PluginRuntimeMode,
    ResolvedAgentModelConfig,
    ResolvedRevisionContent,
    ResolvedSkillSelection,
    ResolvedSubagentEdge,
    SubagentSelection,
    canonical_digest,
)
from .environment_resolution import AgentEnvironmentSelectionResolver, PreparedEnvironmentSelection
from .errors import AgentPresetError, preset_publish_failed
from .models import AgentPresetRecord, AgentPresetRevisionRecord
from .plugin_resolution import AgentPluginSelectionResolver, PluginSelectionError, PreparedPluginSelections
from .validation import AgentConfigValidationError, AgentProtocolPolicy, validate_agent_config

MAX_SUBAGENT_DEPTH = 16
MAX_SUBAGENT_NODES = 256


@dataclass(frozen=True, slots=True)
class PreparedSkill:
    revision_id: str
    skill_id: str
    skill_name: str
    content_digest: str


@dataclass(frozen=True, slots=True)
class PreparedSubagent:
    name: str
    selection: SubagentSelection
    child_revision_id: str
    child_revision_digest: str


@dataclass(frozen=True, slots=True)
class PreparedRevisionResolution:
    actor: AuthenticatedActor
    organization_id: str
    workspace_id: str
    agent_preset_id: str
    config: AgentPresetConfig
    model: PreparedModelExecution
    environment: PreparedEnvironmentSelection | None
    plugins: PreparedPluginSelections
    skills: tuple[PreparedSkill, ...]
    subagents: tuple[PreparedSubagent, ...]


class AgentPresetResolver:
    """Resolve only exact durable resources and recheck them in the commit transaction."""

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
        self._plugin_resolver = plugin_resolver or AgentPluginSelectionResolver(
            sessions,
            runtime_mode=plugin_runtime_mode,
        )
        self.plugin_runtime_mode = plugin_runtime_mode
        self._protocol_policy = protocol_policy or AgentProtocolPolicy()

    async def prepare(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        agent_preset_id: str,
        config: AgentPresetConfig,
    ) -> PreparedRevisionResolution:
        self._validate_local_config(config)
        model = await self._model_selector.prepare(
            organization_id=organization_id,
            workspace_id=workspace_id,
            model_id=config.model.model_config_id,
            invoking_principal=actor.principal,
        )
        async with short_session(self._sessions) as session:
            await authorize_agent_preset(
                session,
                actor=actor,
                workspace_id=workspace_id,
                agent_preset_id=agent_preset_id,
                action=WorkspaceAction.agent_preset_publish,
            )
            try:
                plugins = await self._plugin_resolver.prepare(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    selections=config.plugins,
                )
            except PluginSelectionError as error:
                raise preset_publish_failed(error.reason, path=error.path) from error
            skills = await self._prepare_skills(
                session,
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                agent_preset_id=agent_preset_id,
                config=config,
            )
            subagents = await self._prepare_subagents(
                session,
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                agent_preset_id=agent_preset_id,
                config=config,
            )
        environment = None
        if config.environment is not None:
            if self._environment_resolver is None:
                raise preset_publish_failed("environment_resolution_unavailable", path="environment")
            try:
                environment = await self._environment_resolver.prepare_publication(
                    actor=actor,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    selection=config.environment,
                )
            except EnvironmentManagementError as error:
                raise preset_publish_failed(error.code, path="environment") from error
        return PreparedRevisionResolution(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            agent_preset_id=agent_preset_id,
            config=config,
            model=model,
            environment=environment,
            plugins=plugins,
            skills=skills,
            subagents=subagents,
        )

    async def freeze_in_transaction(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedRevisionResolution,
    ) -> ResolvedRevisionContent:
        await authorize_agent_preset(
            session,
            actor=prepared.actor,
            workspace_id=prepared.workspace_id,
            agent_preset_id=prepared.agent_preset_id,
            action=WorkspaceAction.agent_preset_publish,
        )
        model = await self._model_selector.freeze_in_transaction(session, prepared=prepared.model)
        try:
            plugins = await self._plugin_resolver.freeze_in_transaction(
                session,
                actor=prepared.actor,
                workspace_id=prepared.workspace_id,
                prepared=prepared.plugins,
            )
        except PluginSelectionError as error:
            raise preset_publish_failed(error.reason, path=error.path) from error
        skills = await self._freeze_skills(session, prepared)
        try:
            environment = (
                await self._environment_resolver.freeze_in_transaction(session, prepared=prepared.environment)
                if self._environment_resolver is not None and prepared.environment is not None
                else None
            )
        except EnvironmentManagementError as error:
            raise preset_publish_failed(error.code, path="environment") from error
        subagents = await self._freeze_subagents(session, prepared)
        runtime_lock_digest = canonical_digest(
            {
                "schema_version": "1",
                "mode": self.plugin_runtime_mode.value,
                "plugins": [item.model_dump(mode="json") for item in plugins],
                "child_locks": [item.child_revision_digest for item in prepared.subagents],
                "environment_lock": _environment_lock(environment),
            }
        )
        return ResolvedRevisionContent(
            resolved_model=ResolvedAgentModelConfig(
                execution=model,
                settings=prepared.config.model.settings,
                characteristics=prepared.config.model.characteristics,
            ),
            resolved_plugin_versions=plugins,
            runtime_lock_digest=runtime_lock_digest,
            resolved_skills=skills,
            resolved_environment=environment,
            resolved_subagents=subagents,
        )

    def _validate_local_config(self, config: AgentPresetConfig) -> None:
        if config.input_adapter.adapter_key != "native" or config.input_adapter.config:
            raise preset_publish_failed("input_adapter_unsupported", path="input_adapter")
        if config.environment is not None and self._environment_resolver is None:
            raise preset_publish_failed("environment_resolution_unavailable", path="environment")
        for index, skill in enumerate(config.skills):
            if skill.skill_revision_id in {item.skill_revision_id for item in config.skills[:index]}:
                raise preset_publish_failed("skill_revision_duplicate", path=f"skills.{index}")
        try:
            validate_agent_config(config, protocol_policy=self._protocol_policy)
        except AgentConfigValidationError as error:
            raise preset_publish_failed(error.reason, path=error.path) from error

    async def _prepare_skills(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        agent_preset_id: str,
        config: AgentPresetConfig,
    ) -> tuple[PreparedSkill, ...]:
        if not config.skills:
            return ()
        await authorize_agent_skill_binding(
            session,
            actor=actor,
            workspace_id=workspace_id,
            agent_preset_id=agent_preset_id,
        )
        requested_ids = tuple(item.skill_revision_id for item in config.skills)
        rows = tuple(
            (
                await session.execute(
                    select(SkillRevisionRecord, SkillRecord)
                    .join(SkillRecord, SkillRecord.id == SkillRevisionRecord.skill_id)
                    .where(
                        SkillRevisionRecord.organization_id == organization_id,
                        SkillRevisionRecord.workspace_id == workspace_id,
                        SkillRevisionRecord.id.in_(requested_ids),
                        SkillRecord.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        by_id = {revision.id: (revision, skill) for revision, skill in rows}
        if set(by_id) != set(requested_ids):
            raise preset_publish_failed("skill_revision_not_found", path="skills")
        result: list[PreparedSkill] = []
        names: set[str] = set()
        for revision_id in requested_ids:
            revision, _skill = by_id[revision_id]
            manifest = SkillPackageManifest.model_validate(revision.manifest)
            if manifest.skill_name in names:
                raise preset_publish_failed("skill_name_duplicate", path="skills")
            names.add(manifest.skill_name)
            result.append(
                PreparedSkill(
                    revision_id=revision.id,
                    skill_id=revision.skill_id,
                    skill_name=manifest.skill_name,
                    content_digest=revision.content_digest,
                )
            )
        return tuple(result)

    async def _prepare_subagents(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        agent_preset_id: str,
        config: AgentPresetConfig,
    ) -> tuple[PreparedSubagent, ...]:
        result: list[PreparedSubagent] = []
        for name, selection in config.subagents.items():
            await authorize_agent_preset(
                session,
                actor=actor,
                workspace_id=workspace_id,
                agent_preset_id=selection.agent_preset_id,
                action=WorkspaceAction.agent_preset_read,
            )
            child = await session.scalar(
                select(AgentPresetRecord).where(
                    AgentPresetRecord.id == selection.agent_preset_id,
                    AgentPresetRecord.organization_id == organization_id,
                    AgentPresetRecord.workspace_id == workspace_id,
                )
            )
            if child is None or child.lifecycle_state != "enabled":
                raise preset_publish_failed("subagent_unavailable", path=f"subagents.{name}")
            revision_query = select(AgentPresetRevisionRecord).where(
                AgentPresetRevisionRecord.agent_preset_id == child.id,
                AgentPresetRevisionRecord.organization_id == organization_id,
                AgentPresetRevisionRecord.workspace_id == workspace_id,
            )
            if selection.revision is None:
                if child.active_revision_id is None:
                    raise preset_publish_failed("subagent_not_published", path=f"subagents.{name}")
                revision_query = revision_query.where(AgentPresetRevisionRecord.id == child.active_revision_id)
            else:
                revision_query = revision_query.where(AgentPresetRevisionRecord.revision_number == selection.revision)
            revision = await session.scalar(revision_query)
            if revision is None:
                raise preset_publish_failed("subagent_revision_not_found", path=f"subagents.{name}.revision")
            await self._validate_subagent_graph(
                session,
                root_preset_id=agent_preset_id,
                first_revision=revision,
                path=f"subagents.{name}",
            )
            _validate_child_environment(config, revision, selection.environment, path=f"subagents.{name}.environment")
            result.append(
                PreparedSubagent(
                    name=name,
                    selection=selection,
                    child_revision_id=revision.id,
                    child_revision_digest=revision.content_digest,
                )
            )
        return tuple(result)

    async def _validate_subagent_graph(
        self,
        session: AsyncSession,
        *,
        root_preset_id: str,
        first_revision: AgentPresetRevisionRecord,
        path: str,
    ) -> None:
        pending: list[tuple[AgentPresetRevisionRecord, int]] = [(first_revision, 1)]
        visited: set[str] = set()
        while pending:
            revision, depth = pending.pop()
            if depth > MAX_SUBAGENT_DEPTH:
                raise preset_publish_failed("subagent_graph_too_deep", path=path)
            if revision.agent_preset_id == root_preset_id:
                raise preset_publish_failed("subagent_cycle", path=path)
            if revision.id in visited:
                continue
            visited.add(revision.id)
            if len(visited) > MAX_SUBAGENT_NODES:
                raise preset_publish_failed("subagent_graph_too_large", path=path)
            child_ids = tuple(str(item["child_agent_preset_revision_id"]) for item in revision.resolved_subagents)
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
                raise preset_publish_failed("subagent_revision_not_found", path=path)
            pending.extend((child, depth + 1) for child in children)

    async def _freeze_skills(
        self,
        session: AsyncSession,
        prepared: PreparedRevisionResolution,
    ) -> tuple[ResolvedSkillSelection, ...]:
        if not prepared.skills:
            return ()
        await authorize_agent_skill_binding(
            session,
            actor=prepared.actor,
            workspace_id=prepared.workspace_id,
            agent_preset_id=prepared.agent_preset_id,
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
                raise preset_publish_failed("skill_revision_changed", path="skills")
            result.append(
                ResolvedSkillSelection(
                    skill_revision_id=expected.revision_id,
                    skill_name=expected.skill_name,
                    content_digest=expected.content_digest,
                )
            )
        return tuple(result)

    async def _freeze_subagents(
        self,
        session: AsyncSession,
        prepared: PreparedRevisionResolution,
    ) -> tuple[ResolvedSubagentEdge, ...]:
        result: list[ResolvedSubagentEdge] = []
        for expected in prepared.subagents:
            await authorize_agent_preset(
                session,
                actor=prepared.actor,
                workspace_id=prepared.workspace_id,
                agent_preset_id=expected.selection.agent_preset_id,
                action=WorkspaceAction.agent_preset_read,
            )
            revision = await session.scalar(
                select(AgentPresetRevisionRecord)
                .where(
                    AgentPresetRevisionRecord.id == expected.child_revision_id,
                    AgentPresetRevisionRecord.agent_preset_id == expected.selection.agent_preset_id,
                    AgentPresetRevisionRecord.organization_id == prepared.organization_id,
                    AgentPresetRevisionRecord.workspace_id == prepared.workspace_id,
                )
                .with_for_update()
            )
            if revision is None or revision.content_digest != expected.child_revision_digest:
                raise preset_publish_failed("subagent_revision_changed", path=f"subagents.{expected.name}")
            result.append(
                ResolvedSubagentEdge(
                    name=expected.name,
                    child_agent_preset_id=expected.selection.agent_preset_id,
                    child_agent_preset_revision_id=expected.child_revision_id,
                    description=expected.selection.description,
                    context=expected.selection.context,
                    usage_limits=expected.selection.usage_limits,
                    environment=expected.selection.environment,
                )
            )
        return tuple(result)


def _environment_lock(environment: EnvironmentExecutionConfig | None) -> dict[str, JsonValue] | None:
    if environment is None:
        return None
    return {
        "provider_lock": environment.provider_lock,
        "logical_digest_sha256": environment.logical_digest_sha256,
    }


def _validate_child_environment(
    root_config: AgentPresetConfig,
    child_revision: AgentPresetRevisionRecord,
    policy: ChildEnvironmentPolicy,
    *,
    path: str,
) -> None:
    child_environment = child_revision.resolved_environment
    if policy.mode == "none" and child_environment is not None:
        raise preset_publish_failed("subagent_environment_required", path=path)
    if policy.mode == "shared_root":
        root_environment_id = (
            root_config.environment.environment_revision_id if root_config.environment is not None else None
        )
        child_source_id = (
            str(child_environment.get("source_environment_revision_id")) if child_environment is not None else None
        )
        if root_environment_id is None or child_source_id != root_environment_id:
            raise preset_publish_failed("subagent_environment_incompatible", path=path)
    if policy.mode == "dedicated" and child_environment is None:
        raise preset_publish_failed("subagent_environment_required", path=path)


def resolution_error(error: Exception) -> AgentPresetError:
    """Map an owning-domain resolution failure to one bounded publish error."""

    if isinstance(error, AgentPresetError):
        return error
    return preset_publish_failed("managed_resource_unavailable")
