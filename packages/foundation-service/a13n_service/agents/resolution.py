"""Two-phase Agent Revision-creation resolution."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver, PreparedConnectivity
from a13n_service.environments.authoring import authorize_template
from a13n_service.iam import AuthenticatedActor, authorize_agent, authorize_agent_skill_binding
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.models.runtime import AcceptedModelSelector, PreparedModelExecution
from a13n_service.plugins.runtime import PluginRuntimeLockError
from a13n_service.storage import short_session

from .connectivity_resolution import freeze_revision_connectivity, prepare_revision_connectivity
from .domain import (
    AgentConfig,
    PluginRuntimeMode,
    ResolvedAgentModel,
    ResolvedRevisionContent,
    ResolvedSkillBinding,
    ResolvedSubagentEdge,
    SubagentSelection,
)
from .errors import AgentError, agent_revision_create_failed
from .models import AgentRecord, AgentRevisionRecord
from .plugin_resolution import AgentPluginSelectionResolver, PluginSelectionError, PreparedPluginSelections
from .skill_resolution import (
    PreparedSkillBinding,
    SkillSelectionInvalid,
    freeze_skill_bindings,
    prepare_skill_bindings,
)
from .validation import AgentConfigValidationError, AgentProtocolPolicy, validate_agent_config

MAX_SUBAGENT_DEPTH = 16
MAX_SUBAGENT_NODES = 256


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
    plugins: PreparedPluginSelections
    skills: tuple[PreparedSkillBinding, ...]
    subagents: tuple[PreparedSubagent, ...]
    connectivity: PreparedConnectivity


class AgentResolver:
    """Resolve only exact durable resources and recheck them in the commit transaction."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        model_selector: AcceptedModelSelector,
        *,
        plugin_runtime_mode: PluginRuntimeMode,
        plugin_resolver: AgentPluginSelectionResolver | None = None,
        connectivity_resolver: ConnectivitySelectionResolver | None = None,
        protocol_policy: AgentProtocolPolicy | None = None,
    ) -> None:
        self._sessions = sessions
        self._model_selector = model_selector
        self._plugin_resolver = plugin_resolver or AgentPluginSelectionResolver(
            sessions,
            runtime_mode=plugin_runtime_mode,
        )
        self._connectivity_resolver = connectivity_resolver or ConnectivitySelectionResolver(sessions)
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
            model_key=config.model.model_key,
            settings=config.model.settings,
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
        connectivity = await prepare_revision_connectivity(
            self._connectivity_resolver,
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            config=config,
        )
        return PreparedRevisionResolution(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            config=config,
            model=model,
            plugins=plugins,
            skills=skills,
            subagents=subagents,
            connectivity=connectivity,
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
        subagents = await self._freeze_subagents(session, prepared)
        await freeze_revision_connectivity(self._connectivity_resolver, session, prepared.connectivity)
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
                model_id=model.model_id,
                model_key=model.model_key,
                settings=prepared.config.model.settings,
                characteristics=prepared.config.model.characteristics,
            ),
            resolved_plugin_versions=plugins,
            runtime_lock_digest=runtime_lock.digest,
            resolved_skills=skills,
            connector_tools=prepared.config.connector_tools,
            mcp_tools=prepared.config.mcp_tools,
            resolved_subagents=subagents,
        )

    def _validate_local_config(self, config: AgentConfig) -> None:
        if config.input_adapter.adapter_key != "native" or config.input_adapter.config:
            raise agent_revision_create_failed("input_adapter_unsupported", path="input_adapter")
        for index, skill in enumerate(config.skills):
            if skill.skill_key in {item.skill_key for item in config.skills[:index]}:
                raise agent_revision_create_failed("skill_duplicate", path=f"skills.{index}")
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
    ) -> tuple[PreparedSkillBinding, ...]:
        if not config.skills:
            return ()
        await authorize_agent_skill_binding(
            session,
            actor=actor,
            workspace_id=workspace_id,
            agent_id=agent_id,
        )
        try:
            return await prepare_skill_bindings(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                selections=config.skills,
            )
        except SkillSelectionInvalid as error:
            raise agent_revision_create_failed("skill_selection_invalid", path="skills") from error

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
            await authorize_template(
                session, actor=actor, workspace_id=workspace_id, revision_id=selection.environment.template_revision_id
            )
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
    ) -> tuple[ResolvedSkillBinding, ...]:
        if not prepared.skills:
            return ()
        await authorize_agent_skill_binding(
            session,
            actor=prepared.actor,
            workspace_id=prepared.workspace_id,
            agent_id=prepared.agent_id,
        )
        try:
            return await freeze_skill_bindings(
                session,
                organization_id=prepared.organization_id,
                workspace_id=prepared.workspace_id,
                prepared=prepared.skills,
            )
        except SkillSelectionInvalid as error:
            raise agent_revision_create_failed("skill_selection_invalid", path="skills") from error

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


def resolution_error(error: Exception) -> AgentError:
    """Map an owning-domain resolution failure to one bounded Revision-creation error."""

    if isinstance(error, AgentError):
        return error
    return agent_revision_create_failed("managed_resource_unavailable")
