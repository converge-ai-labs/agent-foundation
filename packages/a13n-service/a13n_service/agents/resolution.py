"""Two-phase Agent Revision-creation resolution."""

from __future__ import annotations

from dataclasses import dataclass, field

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.memory import MemoryProviderDefinition
from a13n_harness.providers.web.builtins import built_in_web_providers
from a13n_harness.providers.web.definition import WebProviderDefinition
from a13n_harness.toolsets.file_media import NativeInputMediaKind
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver, PreparedConnectivity
from a13n_service.environments.authoring import authorize_template
from a13n_service.iam import AuthenticatedActor, authorize_agent, authorize_agent_skill_binding, authorize_workspace
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.memory.domain import memory_provider_ids
from a13n_service.memory.resources import MemoryProviderError, require_memory_configuration
from a13n_service.models.runtime import AcceptedModelSelector, PreparedModelExecution
from a13n_service.storage import short_session
from a13n_service.web.domain import ScrapeSelection, provider_selections
from a13n_service.web.resources import WebProviderError, require_operation
from a13n_service.web.resources import require_provider as require_web_provider

from .connectivity_resolution import freeze_revision_connectivity, prepare_revision_connectivity
from .domain import (
    AgentConfig,
    ResolvedAgentModel,
    ResolvedRevisionContent,
    ResolvedSkillBinding,
    ResolvedSubagentEdge,
    SubagentSelection,
)
from .errors import AgentError, agent_revision_create_failed
from .models import AgentRecord, AgentRevisionRecord
from .skill_resolution import (
    PreparedSkillBinding,
    SkillSelectionInvalid,
    freeze_skill_bindings,
    prepare_skill_bindings,
)
from .toolsets import web_selection
from .validation import AgentConfigValidationError, AgentProtocolPolicy, validate_agent_config

MAX_SUBAGENT_DEPTH = 16
MAX_SUBAGENT_NODES = 256


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
    agent_id: str
    config: AgentConfig
    model: PreparedModelExecution
    skills: tuple[PreparedSkillBinding, ...]
    subagents: tuple[PreparedSubagent, ...]
    connectivity: PreparedConnectivity
    media_models: dict[NativeInputMediaKind, PreparedModelExecution] = field(default_factory=dict)
    reviewer_model: PreparedModelExecution | None = None
    creation: bool = False
    authorization_agent_id: str | None = None


class AgentResolver:
    """Resolve only exact durable resources and recheck them in the commit transaction."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        model_selector: AcceptedModelSelector,
        *,
        connectivity_resolver: ConnectivitySelectionResolver | None = None,
        protocol_policy: AgentProtocolPolicy | None = None,
        web_provider_catalog: ProviderCatalog[WebProviderDefinition] | None = None,
        memory_provider_catalog: ProviderCatalog[MemoryProviderDefinition] | None = None,
    ) -> None:
        self._sessions = sessions
        self._model_selector = model_selector
        self._connectivity_resolver = connectivity_resolver or ConnectivitySelectionResolver(sessions)
        self._protocol_policy = protocol_policy or AgentProtocolPolicy()
        self._web_provider_catalog = web_provider_catalog or ProviderCatalog(built_in_web_providers())
        self._memory_provider_catalog = (
            memory_provider_catalog if memory_provider_catalog is not None else ProviderCatalog()
        )

    async def prepare(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        agent_id: str,
        config: AgentConfig,
        creation: bool = False,
        authorization_agent_id: str | None = None,
    ) -> PreparedRevisionResolution:
        self._validate_local_config(config)
        model = await self._model_selector.prepare(
            organization_id=organization_id,
            workspace_id=workspace_id,
            model_key=config.model.model_key,
            settings=config.model.settings,
        )
        media_models = await self._model_selector.prepare_media_selection(
            organization_id=organization_id, workspace_id=workspace_id, selection=config.media_understanding
        )
        reviewer_model = (
            await self._model_selector.prepare(
                organization_id=organization_id,
                workspace_id=workspace_id,
                model_id=config.reviewer.model,
                settings=config.reviewer.model_settings or {},
            )
            if config.reviewer is not None
            else None
        )
        async with short_session(self._sessions) as session:
            await _authorize_revision(
                session,
                actor=actor,
                workspace_id=workspace_id,
                agent_id=authorization_agent_id or agent_id,
                creation=creation,
            )
            await authorize_template(
                session,
                actor=actor,
                workspace_id=workspace_id,
                template_id=config.default_environment_template_id,
            )
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
            skills=skills,
            subagents=subagents,
            connectivity=connectivity,
            media_models=media_models,
            reviewer_model=reviewer_model,
            creation=creation,
            authorization_agent_id=authorization_agent_id,
        )

    async def freeze_in_transaction(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedRevisionResolution,
    ) -> ResolvedRevisionContent:
        await _authorize_revision(
            session,
            actor=prepared.actor,
            workspace_id=prepared.workspace_id,
            agent_id=prepared.authorization_agent_id or prepared.agent_id,
            creation=prepared.creation,
        )
        await authorize_template(
            session,
            actor=prepared.actor,
            workspace_id=prepared.workspace_id,
            template_id=prepared.config.default_environment_template_id,
        )
        if prepared.config.memory is not None:
            if memory_provider_ids(prepared.config.memory):
                await authorize_workspace(
                    session,
                    actor=prepared.actor,
                    workspace_id=prepared.workspace_id,
                    action=WorkspaceAction.memory_provider_read,
                )
            await require_memory_configuration(
                session,
                selection=prepared.config.memory,
                organization_id=prepared.organization_id,
                workspace_id=prepared.workspace_id,
                catalog=self._memory_provider_catalog,
            )
        for operation, selection in provider_selections(web_selection(prepared.config.toolsets)):
            provider = await require_web_provider(
                session,
                organization_id=prepared.organization_id,
                workspace_id=prepared.workspace_id,
                provider_id=selection.provider_id,
                eligible=True,
                catalog=self._web_provider_catalog,
            )
            require_operation(
                provider,
                operation,
                self._web_provider_catalog,
                selection=selection if isinstance(selection, ScrapeSelection) else None,
            )
        model = await self._model_selector.freeze_in_transaction(session, prepared=prepared.model)
        for media_model in prepared.media_models.values():
            await self._model_selector.freeze_in_transaction(session, prepared=media_model)
        if prepared.reviewer_model is not None:
            await self._model_selector.freeze_in_transaction(session, prepared=prepared.reviewer_model)
        skills = await self._freeze_skills(session, prepared)
        subagents = await self._freeze_subagents(session, prepared)
        await freeze_revision_connectivity(self._connectivity_resolver, session, prepared.connectivity)
        return ResolvedRevisionContent(
            resolved_model=ResolvedAgentModel(
                model_id=model.model_id,
                model_key=model.model_key,
                settings=prepared.config.model.settings,
                characteristics=prepared.config.model.characteristics,
            ),
            resolved_skills=skills,
            connection_tools=prepared.config.connection_tools,
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
                revision_query = revision_query.where(AgentRevisionRecord.id == child.default_revision_id)
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
            if revision is None or revision.content_digest != expected.child_revision_digest:
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


async def _authorize_revision(
    session: AsyncSession, *, actor: AuthenticatedActor, workspace_id: str, agent_id: str, creation: bool
) -> None:
    if creation:
        await authorize_workspace(session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.agent_create)
    else:
        await authorize_agent(
            session,
            actor=actor,
            workspace_id=workspace_id,
            agent_id=agent_id,
            action=WorkspaceAction.agent_revision_create,
        )


def resolution_error(error: Exception) -> AgentError:
    """Map an owning-domain resolution failure to one bounded Revision-creation error."""

    if isinstance(error, AgentError):
        return error
    if isinstance(error, WebProviderError | MemoryProviderError):
        return AgentError(error.code, error.message, category=error.category)
    return agent_revision_create_failed("managed_resource_unavailable")
