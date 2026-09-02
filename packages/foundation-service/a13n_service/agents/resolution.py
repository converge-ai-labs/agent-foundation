"""Two-phase Agent Revision-creation resolution."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.iam import AuthenticatedActor, authorize_agent, authorize_agent_skill_binding
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.models.runtime import AcceptedModelSelector, PreparedModelExecution
from a13n_service.plugins.runtime import PluginRuntimeLockError
from a13n_service.skills.domain import SkillPackageManifest
from a13n_service.skills.models import SkillRecord, SkillRevisionRecord
from a13n_service.storage import short_session

from .domain import (
    AgentConfig,
    ChildEnvironmentPolicy,
    EnvironmentExecutionConfig,
    PluginRuntimeMode,
    ResolvedAgentModel,
    ResolvedRevisionContent,
    ResolvedSkillSelection,
    ResolvedSubagentEdge,
    SubagentSelection,
)
from .environment_resolution import AgentEnvironmentSelectionResolver, PreparedEnvironmentSelection
from .errors import AgentError, agent_revision_create_failed
from .models import AgentRecord, AgentRevisionRecord
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
    child_runtime_lock_digest: str


@dataclass(frozen=True, slots=True)
class PreparedRevisionResolution:
    actor: AuthenticatedActor
    organization_id: str
    workspace_id: str
    agent_id: str
    config: AgentConfig
    model: PreparedModelExecution
    environment: PreparedEnvironmentSelection | None
    plugins: PreparedPluginSelections
    skills: tuple[PreparedSkill, ...]
    subagents: tuple[PreparedSubagent, ...]


class AgentResolver:
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
        agent_id: str,
        config: AgentConfig,
    ) -> PreparedRevisionResolution:
        self._validate_local_config(config)
        model = await self._model_selector.prepare(
            organization_id=organization_id,
            workspace_id=workspace_id,
            model_revision_id=config.model.model_revision_id,
            invoking_principal=actor.principal,
        )
        async with short_session(self._sessions) as session:
            await authorize_agent(
                session,
                actor=actor,
                workspace_id=workspace_id,
                agent_id=agent_id,
                action=WorkspaceAction.agent_revision_create,
            )
            try:
                plugins = await self._plugin_resolver.prepare(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    selections=config.plugins,
                )
            except PluginSelectionError as error:
                raise agent_revision_create_failed(error.reason, path=error.path) from error
            skills = await self._prepare_skills(
                session,
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                config=config,
            )
            subagents = await self._prepare_subagents(
                session,
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                config=config,
            )
        environment = None
        if config.environment is not None:
            if self._environment_resolver is None:
                raise agent_revision_create_failed("environment_resolution_unavailable", path="environment")
            try:
                environment = await self._environment_resolver.prepare_revision_creation(
                    actor=actor,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    selection=config.environment,
                )
            except EnvironmentManagementError as error:
                raise agent_revision_create_failed(error.code, path="environment") from error
        _require_writable_skill_environment(config.skills, environment)
        return PreparedRevisionResolution(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
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
        await authorize_agent(
            session,
            actor=prepared.actor,
            workspace_id=prepared.workspace_id,
            agent_id=prepared.agent_id,
            action=WorkspaceAction.agent_revision_create,
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
            raise agent_revision_create_failed(error.reason, path=error.path) from error
        skills = await self._freeze_skills(session, prepared)
        try:
            environment = (
                await self._environment_resolver.freeze_in_transaction(session, prepared=prepared.environment)
                if self._environment_resolver is not None and prepared.environment is not None
                else None
            )
        except EnvironmentManagementError as error:
            raise agent_revision_create_failed(error.code, path="environment") from error
        _require_writable_skill_environment(skills, environment)
        subagents = await self._freeze_subagents(session, prepared)
        try:
            runtime_lock = await self._plugin_resolver.freeze_runtime_lock(
                session,
                prepared=prepared.plugins,
                child_lock_digests=tuple(item.child_runtime_lock_digest for item in prepared.subagents),
                use_active_catalog=True,
            )
        except PluginRuntimeLockError as error:
            raise agent_revision_create_failed(error.reason, path=error.path) from error
        return ResolvedRevisionContent(
            resolved_model=ResolvedAgentModel(
                execution=model,
                settings=prepared.config.model.settings,
                characteristics=prepared.config.model.characteristics,
            ),
            resolved_plugin_versions=plugins,
            runtime_lock_digest=runtime_lock.digest,
            resolved_skills=skills,
            resolved_environment=environment,
            resolved_subagents=subagents,
        )

    def _validate_local_config(self, config: AgentConfig) -> None:
        if config.input_adapter.adapter_key != "native" or config.input_adapter.config:
            raise agent_revision_create_failed("input_adapter_unsupported", path="input_adapter")
        if config.environment is not None and self._environment_resolver is None:
            raise agent_revision_create_failed("environment_resolution_unavailable", path="environment")
        if config.skills and config.environment is None:
            raise agent_revision_create_failed("skill_environment_required", path="environment")
        for index, skill in enumerate(config.skills):
            if skill.skill_revision_id in {item.skill_revision_id for item in config.skills[:index]}:
                raise agent_revision_create_failed("skill_revision_duplicate", path=f"skills.{index}")
        try:
            validate_agent_config(config, protocol_policy=self._protocol_policy)
        except AgentConfigValidationError as error:
            raise agent_revision_create_failed(error.reason, path=error.path) from error

    async def _prepare_skills(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        agent_id: str,
        config: AgentConfig,
    ) -> tuple[PreparedSkill, ...]:
        if not config.skills:
            return ()
        await authorize_agent_skill_binding(
            session,
            actor=actor,
            workspace_id=workspace_id,
            agent_id=agent_id,
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
            raise agent_revision_create_failed("skill_revision_not_found", path="skills")
        result: list[PreparedSkill] = []
        names: set[str] = set()
        for revision_id in requested_ids:
            revision, _skill = by_id[revision_id]
            try:
                manifest = SkillPackageManifest.model_validate(revision.manifest)
            except ValidationError as error:
                raise agent_revision_create_failed("skill_revision_invalid", path="skills") from error
            if manifest.content_digest != revision.content_digest:
                raise agent_revision_create_failed("skill_revision_invalid", path="skills")
            if manifest.skill_name in names:
                raise agent_revision_create_failed("skill_name_duplicate", path="skills")
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
        agent_id: str,
        config: AgentConfig,
    ) -> tuple[PreparedSubagent, ...]:
        result: list[PreparedSubagent] = []
        for name, selection in config.subagents.items():
            await authorize_agent(
                session,
                actor=actor,
                workspace_id=workspace_id,
                agent_id=selection.agent_id,
                action=WorkspaceAction.agent_read,
            )
            child = await session.scalar(
                select(AgentRecord).where(
                    AgentRecord.id == selection.agent_id,
                    AgentRecord.organization_id == organization_id,
                    AgentRecord.workspace_id == workspace_id,
                )
            )
            if child is None or not child.enabled or child.archived_at is not None:
                raise agent_revision_create_failed("subagent_unavailable", path=f"subagents.{name}")
            revision_query = select(AgentRevisionRecord).where(
                AgentRevisionRecord.agent_id == child.id,
                AgentRevisionRecord.organization_id == organization_id,
                AgentRevisionRecord.workspace_id == workspace_id,
            )
            if selection.version is None:
                revision_query = revision_query.where(AgentRevisionRecord.id == child.current_revision_id)
            else:
                revision_query = revision_query.where(AgentRevisionRecord.version == selection.version)
            revision = await session.scalar(revision_query)
            if revision is None:
                raise agent_revision_create_failed("subagent_revision_not_found", path=f"subagents.{name}.version")
            await self._validate_subagent_graph(
                session,
                root_agent_id=agent_id,
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
                    child_runtime_lock_digest=revision.runtime_lock_digest,
                )
            )
        return tuple(result)

    async def _validate_subagent_graph(
        self,
        session: AsyncSession,
        *,
        root_agent_id: str,
        first_revision: AgentRevisionRecord,
        path: str,
    ) -> None:
        pending: list[tuple[AgentRevisionRecord, int]] = [(first_revision, 1)]
        visited: set[str] = set()
        while pending:
            revision, depth = pending.pop()
            if depth > MAX_SUBAGENT_DEPTH:
                raise agent_revision_create_failed("subagent_graph_too_deep", path=path)
            if revision.agent_id == root_agent_id:
                raise agent_revision_create_failed("subagent_cycle", path=path)
            if revision.id in visited:
                continue
            visited.add(revision.id)
            if len(visited) > MAX_SUBAGENT_NODES:
                raise agent_revision_create_failed("subagent_graph_too_large", path=path)
            child_ids = tuple(str(item["child_agent_revision_id"]) for item in revision.resolved_subagents)
            if not child_ids:
                continue
            children = tuple(
                (await session.scalars(select(AgentRevisionRecord).where(AgentRevisionRecord.id.in_(child_ids)))).all()
            )
            if len(children) != len(set(child_ids)):
                raise agent_revision_create_failed("subagent_revision_not_found", path=path)
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
            agent_id=prepared.agent_id,
        )
        ids = tuple(item.revision_id for item in prepared.skills)
        rows = tuple(
            (
                await session.scalars(
                    select(SkillRevisionRecord)
                    .join(SkillRecord, SkillRecord.id == SkillRevisionRecord.skill_id)
                    .where(
                        SkillRevisionRecord.organization_id == prepared.organization_id,
                        SkillRevisionRecord.workspace_id == prepared.workspace_id,
                        SkillRevisionRecord.id.in_(ids),
                        SkillRecord.organization_id == prepared.organization_id,
                        SkillRecord.workspace_id == prepared.workspace_id,
                        SkillRecord.deleted_at.is_(None),
                    )
                    .with_for_update()
                )
            ).all()
        )
        current = {item.id: item for item in rows}
        result: list[ResolvedSkillSelection] = []
        for expected in prepared.skills:
            row = current.get(expected.revision_id)
            try:
                manifest = SkillPackageManifest.model_validate(row.manifest) if row is not None else None
            except ValidationError as error:
                raise agent_revision_create_failed("skill_revision_changed", path="skills") from error
            if (
                row is None
                or manifest is None
                or row.skill_id != expected.skill_id
                or row.content_digest != expected.content_digest
                or manifest.content_digest != expected.content_digest
                or manifest.skill_name != expected.skill_name
            ):
                raise agent_revision_create_failed("skill_revision_changed", path="skills")
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
            await authorize_agent(
                session,
                actor=prepared.actor,
                workspace_id=prepared.workspace_id,
                agent_id=expected.selection.agent_id,
                action=WorkspaceAction.agent_read,
            )
            revision = await session.scalar(
                select(AgentRevisionRecord)
                .where(
                    AgentRevisionRecord.id == expected.child_revision_id,
                    AgentRevisionRecord.agent_id == expected.selection.agent_id,
                    AgentRevisionRecord.organization_id == prepared.organization_id,
                    AgentRevisionRecord.workspace_id == prepared.workspace_id,
                )
                .with_for_update()
            )
            if (
                revision is None
                or revision.content_digest != expected.child_revision_digest
                or revision.runtime_lock_digest != expected.child_runtime_lock_digest
            ):
                raise agent_revision_create_failed("subagent_revision_changed", path=f"subagents.{expected.name}")
            result.append(
                ResolvedSubagentEdge(
                    name=expected.name,
                    child_agent_id=expected.selection.agent_id,
                    child_agent_revision_id=expected.child_revision_id,
                    description=expected.selection.description,
                    context=expected.selection.context,
                    usage_limits=expected.selection.usage_limits,
                    environment=expected.selection.environment,
                )
            )
        return tuple(result)


def _validate_child_environment(
    root_config: AgentConfig,
    child_revision: AgentRevisionRecord,
    policy: ChildEnvironmentPolicy,
    *,
    path: str,
) -> None:
    child_environment = child_revision.resolved_environment
    if policy.mode == "none" and child_environment is not None:
        raise agent_revision_create_failed("subagent_environment_required", path=path)
    if policy.mode == "shared_root":
        root_environment_id = (
            root_config.environment.environment_revision_id if root_config.environment is not None else None
        )
        child_source_id = (
            str(child_environment.get("source_environment_revision_id")) if child_environment is not None else None
        )
        if root_environment_id is None or child_source_id != root_environment_id:
            raise agent_revision_create_failed("subagent_environment_incompatible", path=path)
    if policy.mode == "dedicated" and child_environment is None:
        raise agent_revision_create_failed("subagent_environment_required", path=path)


def _require_writable_skill_environment(
    skills: Sequence[object],
    environment: PreparedEnvironmentSelection | EnvironmentExecutionConfig | None,
) -> None:
    if not skills:
        return
    resolved = environment.resolved if isinstance(environment, PreparedEnvironmentSelection) else environment
    if resolved is None:
        raise agent_revision_create_failed("skill_environment_required", path="environment")
    if resolved.access == "read_only":
        raise agent_revision_create_failed("skill_environment_not_writable", path="environment")


def resolution_error(error: Exception) -> AgentError:
    """Map an owning-domain resolution failure to one bounded Revision-creation error."""

    if isinstance(error, AgentError):
        return error
    return agent_revision_create_failed("managed_resource_unavailable")
