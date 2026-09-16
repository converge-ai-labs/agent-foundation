"""Deployment-authorized synchronization of the hidden same-Workspace assistant."""

from __future__ import annotations

from a13n_logging import get_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import AgentConfig, AgentRevisionCreateResult, new_agent_id, new_agent_revision_id
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.agents.persistence import new_revision
from a13n_service.agents.resolution import AgentResolver
from a13n_service.agents.toolsets import default_toolsets
from a13n_service.digests import digest_request
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.audit import SystemAuditActor, security_audit_record
from a13n_service.iam.models import WorkspaceRecord
from a13n_service.ids import new_object_id
from a13n_service.resource_keys import insert_with_key
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, utc_now

from .authorization import authorize_session
from .context import DefinitionIdentity
from .definition import READ_TOOLS, AssistantDefinition
from .persistence import failure
from .readiness import SelectedAssistantModel

logger = get_logger(__name__)
SYSTEM_ACTOR_ID = "sys_configurationassistant"


class SystemConfigurationAgent:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        resolver: AgentResolver,
        definition: AssistantDefinition,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions, self._resolver, self._definition, self._clock = sessions, resolver, definition, clock

    async def ensure(
        self, *, actor: AuthenticatedActor, session_id: str, model: SelectedAssistantModel
    ) -> AgentRevisionCreateResult:
        identity = DefinitionIdentity(
            generation=self._definition.generation,
            content_digest=digest_request(self._definition),
            knowledge_bundle=self._definition.knowledge_bundle,
        )
        async with short_session(self._sessions) as session:
            conversation = await authorize_session(session, actor=actor, session_id=session_id)
            organization_id, target_id = conversation.organization_id, conversation.configuration_target_agent_id
            current = await _selected(session, workspace_id=actor.workspace_id)
            if current is not None:
                cached = _compatible(current, identity)
                if cached is not None:
                    return cached
            agent_id = new_agent_id() if current is None else current[0].id
        config = assistant_config(self._definition, model)
        prepared = await self._resolver.prepare(
            actor=actor,
            organization_id=organization_id,
            workspace_id=actor.workspace_id,
            agent_id=agent_id,
            config=config,
            creation=target_id is None,
            authorization_agent_id=target_id,
        )
        async with transaction(self._sessions, sqlite_immediate=True) as session:
            # The workspace is the uniqueness owner, even before an assistant exists.
            await session.scalar(
                select(WorkspaceRecord).where(WorkspaceRecord.id == actor.workspace_id).with_for_update()
            )
            await authorize_session(session, actor=actor, session_id=session_id)
            current = await _selected(session, workspace_id=actor.workspace_id)
            if current is not None:
                cached = _compatible(current, identity)
                if cached is not None:
                    return cached
            resolved = await self._resolver.freeze_in_transaction(session, prepared=prepared)
            now = self._clock()
            if current is None:
                agent = AgentRecord(
                    id=agent_id,
                    organization_id=organization_id,
                    workspace_id=actor.workspace_id,
                    source="builtin",
                    system_purpose="configuration_assistant",
                    name=self._definition.name,
                    description="System-maintained configuration assistant",
                    labels={},
                    version=1,
                    current_revision_id=new_agent_revision_id(),
                    enabled=True,
                    archived_at=None,
                    created_by_type="system",
                    created_by_id=SYSTEM_ACTOR_ID,
                    updated_by_type="system",
                    updated_by_id=SYSTEM_ACTOR_ID,
                    created_at=now,
                    updated_at=now,
                )
                await insert_with_key(session, agent, prefix="agent")
            else:
                agent = current[0]
            revision = new_revision(
                agent,
                revision_id=agent.current_revision_id if current is None else new_agent_revision_id(),
                version=1 if current is None else agent.version + 1,
                config=config,
                resolved=resolved,
                source_revision_id=None,
                actor=actor,
                now=now,
            )
            revision.created_by_type, revision.created_by_id = "system", SYSTEM_ACTOR_ID
            revision.system_definition = identity.model_dump(mode="json")
            session.add(revision)
            agent.current_revision_id, agent.version = revision.id, revision.version
            agent.updated_by_type, agent.updated_by_id, agent.updated_at = "system", SYSTEM_ACTOR_ID, now
            session.add(
                security_audit_record(
                    audit_id=new_object_id("audit"),
                    actor=SystemAuditActor(request_id=actor.request_id),
                    organization_id=organization_id,
                    workspace_id=actor.workspace_id,
                    action="agent.configuration_assistant.synchronize",
                    resource_type="agent",
                    resource_id=agent.id,
                    outcome="success",
                    occurred_at=now,
                    details={
                        "generation": identity.generation,
                        "definition_digest": identity.content_digest,
                        "agent_revision_id": revision.id,
                    },
                )
            )
            await session.flush()
            result = AgentRevisionCreateResult(agent=agent.to_resource(), revision=revision.to_resource())
        logger.info(
            "configuration_assistant_synchronized",
            extra={
                "agent_id": result.agent.id,
                "agent_revision_id": result.revision.id,
                "definition_generation": identity.generation,
            },
        )
        return result


async def _selected(session: AsyncSession, *, workspace_id: str) -> tuple[AgentRecord, AgentRevisionRecord] | None:
    row = (
        await session.execute(
            select(AgentRecord, AgentRevisionRecord)
            .join(AgentRevisionRecord, AgentRevisionRecord.id == AgentRecord.current_revision_id)
            .where(AgentRecord.workspace_id == workspace_id, AgentRecord.system_purpose == "configuration_assistant")
        )
    ).one_or_none()
    return None if row is None else (row[0], row[1])


def _compatible(
    current: tuple[AgentRecord, AgentRevisionRecord], identity: DefinitionIdentity
) -> AgentRevisionCreateResult | None:
    agent, revision = current
    previous = DefinitionIdentity.model_validate(revision.system_definition)
    if previous == identity:
        return AgentRevisionCreateResult(agent=agent.to_resource(), revision=revision.to_resource())
    if previous.generation >= identity.generation:
        raise failure(
            "configuration_definition_incompatible",
            "This process cannot accept work for the current assistant definition.",
        )
    return None


def assistant_config(definition: AssistantDefinition, model: SelectedAssistantModel) -> AgentConfig:
    toolsets = default_toolsets()
    for key, selected in toolsets.items():
        toolsets[key] = selected.model_copy(
            update={
                "enabled": key == "files",
                "tools": {
                    name: tool.model_copy(
                        update={
                            "enabled": key == "files" and name in READ_TOOLS,
                            "permission": "allow" if key == "files" and name in READ_TOOLS else "deny",
                        }
                    )
                    for name, tool in selected.tools.items()
                },
            }
        )
    return AgentConfig.model_validate(
        {
            "model": {"model_key": model.model_key, "settings": {}},
            "instructions": definition.instructions,
            "input_adapter": {"adapter_key": "native"},
            "protocol": {"public_name": definition.name},
            "toolsets": toolsets,
        }
    )
