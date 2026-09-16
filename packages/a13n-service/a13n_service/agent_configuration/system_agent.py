"""Provision the hidden same-Workspace identity; definitions belong to Run snapshots."""

from __future__ import annotations

from a13n_logging import get_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import Agent, AgentConfig, new_agent_id
from a13n_service.agents.models import AgentRecord
from a13n_service.agents.toolsets import default_toolsets
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.audit import SystemAuditActor, security_audit_record
from a13n_service.iam.models import WorkspaceRecord
from a13n_service.ids import new_object_id
from a13n_service.resource_keys import insert_with_key
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from .authorization import authorize_session
from .definition import READ_TOOLS, AssistantDefinition
from .readiness import SelectedAssistantModel

logger = get_logger(__name__)
SYSTEM_ACTOR_ID = "sys_configurationassistant"


class SystemConfigurationAgent:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], definition: AssistantDefinition, *, clock: Clock = utc_now
    ) -> None:
        self._sessions, self._definition, self._clock = sessions, definition, clock

    async def ensure(self, *, actor: AuthenticatedActor, session_id: str) -> Agent:
        async with transaction(self._sessions, sqlite_immediate=True) as session:
            # Workspace locking serializes first creation before the identity exists.
            await session.scalar(
                select(WorkspaceRecord).where(WorkspaceRecord.id == actor.workspace_id).with_for_update()
            )
            conversation = await authorize_session(session, actor=actor, session_id=session_id)
            agent = await session.scalar(
                select(AgentRecord).where(
                    AgentRecord.workspace_id == actor.workspace_id,
                    AgentRecord.system_purpose == "configuration_assistant",
                )
            )
            if agent is not None:
                return agent.to_resource()
            now = self._clock()
            agent = AgentRecord(
                id=new_agent_id(),
                organization_id=conversation.organization_id,
                workspace_id=actor.workspace_id,
                source="builtin",
                system_purpose="configuration_assistant",
                name=self._definition.name,
                description="System-maintained configuration assistant",
                labels={},
                version=1,
                current_revision_id=None,
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
            session.add(
                security_audit_record(
                    audit_id=new_object_id("audit"),
                    actor=SystemAuditActor(request_id=actor.request_id),
                    organization_id=conversation.organization_id,
                    workspace_id=actor.workspace_id,
                    action="agent.configuration_assistant.provision",
                    resource_type="agent",
                    resource_id=agent.id,
                    outcome="success",
                    occurred_at=now,
                    details={},
                )
            )
            await session.flush()
            result = agent.to_resource()
        logger.info("configuration_assistant_provisioned", extra={"agent_id": result.id})
        return result


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
            "model": {"model_key": model.model_key, "settings": model.settings},
            "instructions": definition.instructions,
            "input_adapter": {"adapter_key": "native"},
            "protocol": {"public_name": definition.name},
            "toolsets": toolsets,
        }
    )
