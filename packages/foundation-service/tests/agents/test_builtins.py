from __future__ import annotations

import pytest
from a13n_service.agents.domain import (
    BuiltinAgentRegistration,
    CreateAgentRequest,
    CreateAgentRevisionRequest,
    DuplicateAgentRequest,
    UpdateAgentRequest,
)
from a13n_service.agents.errors import AgentError
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.agents.service import AgentService
from a13n_service.etags import resource_etag
from a13n_service.models.models import ModelRevisionRecord
from a13n_service.storage import transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import MODEL_REVISION_ID, WORKSPACE_ID, actor, agent_config

BUILTIN_AGENT_ID = "ap_builtinagent0001"
SYSTEM_ACTOR_ID = "sa_1234567890abcdef"


def registration(*, instructions: str = "Be helpful.", name: str = "Foundation Assistant") -> BuiltinAgentRegistration:
    return BuiltinAgentRegistration(
        agent_id=BUILTIN_AGENT_ID,
        system_actor_id=SYSTEM_ACTOR_ID,
        name=name,
        description="Distribution-owned starter Agent.",
        config=agent_config(instructions=instructions),
    )


@pytest.mark.anyio
async def test_builtin_registration_is_executable_idempotent_and_upgradable(
    agent_service: AgentService,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    first = await agent_service.register_builtin(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(),
    )

    assert first.agent.id == BUILTIN_AGENT_ID
    assert first.agent.source == "builtin"
    assert first.agent.enabled is True
    assert first.agent.current_revision_id == first.revision.id
    assert first.agent.version == first.revision.version == 1
    assert first.revision.created_by.principal_id == SYSTEM_ACTOR_ID

    replay = await agent_service.register_builtin(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(),
    )
    assert replay == first

    upgraded = await agent_service.register_builtin(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(instructions="Use the upgraded behavior."),
    )
    assert upgraded.agent.version == upgraded.revision.version == 2
    assert upgraded.agent.current_revision_id == upgraded.revision.id
    assert upgraded.revision.config.instructions == "Use the upgraded behavior."

    revisions = await agent_service.list_revisions(
        actor=actor(),
        agent_id=BUILTIN_AGENT_ID,
        limit=10,
        cursor=None,
    )
    assert tuple(item.version for item in revisions.items) == (2, 1)
    async with transaction(agent_sessions) as session:
        agent_record = await session.get(AgentRecord, BUILTIN_AGENT_ID)
        revision_records = tuple(
            (
                await session.scalars(
                    select(AgentRevisionRecord).where(AgentRevisionRecord.agent_id == BUILTIN_AGENT_ID)
                )
            ).all()
        )
    assert agent_record is not None
    assert agent_record.created_by_type == "system"
    assert agent_record.updated_by_type == "system"
    assert {item.created_by_type for item in revision_records} == {"system"}


@pytest.mark.anyio
async def test_builtin_metadata_update_does_not_create_a_revision(agent_service: AgentService) -> None:
    first = await agent_service.register_builtin(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(),
    )
    renamed = await agent_service.register_builtin(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(name="Foundation Helper"),
    )

    assert renamed.agent.name == "Foundation Helper"
    assert renamed.agent.version == 1
    assert renamed.revision == first.revision
    revisions = await agent_service.list_revisions(
        actor=actor(),
        agent_id=BUILTIN_AGENT_ID,
        limit=10,
        cursor=None,
    )
    assert len(revisions.items) == 1


@pytest.mark.anyio
async def test_builtin_registration_race_replay_requires_exact_committed_result(
    agent_service: AgentService,
) -> None:
    registered = await agent_service.register_builtin(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(),
    )

    replay = await agent_service._builtin_registration_replay(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(),
        expected_content_digest=registered.revision.content_digest,
    )
    assert replay == registered
    assert (
        await agent_service._builtin_registration_replay(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            registration=registration(name="Different metadata"),
            expected_content_digest=registered.revision.content_digest,
        )
        is None
    )
    assert (
        await agent_service._builtin_registration_replay(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            registration=registration(),
            expected_content_digest="0" * 64,
        )
        is None
    )


@pytest.mark.anyio
async def test_builtin_registration_revisions_changed_resolved_dependencies(
    agent_service: AgentService,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    first = await agent_service.register_builtin(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(),
    )
    async with transaction(agent_sessions) as session:
        model = await session.get(ModelRevisionRecord, MODEL_REVISION_ID)
        assert model is not None
        model.model_name = "gpt-5.1"

    upgraded = await agent_service.register_builtin(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(),
    )

    assert upgraded.revision.version == 2
    assert upgraded.revision.config == first.revision.config
    assert upgraded.revision.resolved_model.execution.model_name == "gpt-5.1"
    assert upgraded.revision.content_digest != first.revision.content_digest
    assert upgraded.agent.current_revision_id == upgraded.revision.id


@pytest.mark.anyio
async def test_builtin_is_user_read_only_but_can_be_duplicated(agent_service: AgentService) -> None:
    registered = await agent_service.register_builtin(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        registration=registration(),
    )

    with pytest.raises(AgentError) as metadata_rejected:
        await agent_service.patch_metadata(
            actor=actor(),
            agent_id=BUILTIN_AGENT_ID,
            if_match=resource_etag(registered.agent.id, registered.agent.updated_at),
            request=UpdateAgentRequest(name="User mutation"),
        )
    assert metadata_rejected.value.code == "agent_state_conflict"

    with pytest.raises(AgentError) as revision_rejected:
        await agent_service.create_revision(
            actor=actor(),
            agent_id=BUILTIN_AGENT_ID,
            idempotency_key="builtin-user-revision",
            request=CreateAgentRevisionRequest(
                expected_version=registered.agent.version,
                config=agent_config(instructions="User mutation."),
            ),
        )
    assert revision_rejected.value.code == "agent_state_conflict"

    duplicate = await agent_service.duplicate(
        actor=actor(),
        agent_id=BUILTIN_AGENT_ID,
        idempotency_key="duplicate-builtin",
        request=DuplicateAgentRequest(
            expected_version=registered.agent.version,
            name="Customized Assistant",
        ),
    )
    assert duplicate.source == "custom"
    assert duplicate.enabled is True
    assert duplicate.duplicated_from_agent_id == BUILTIN_AGENT_ID
    assert duplicate.duplicated_from_revision_id == registered.revision.id


@pytest.mark.anyio
async def test_failed_builtin_registration_creates_no_partial_agent(
    agent_service: AgentService,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    invalid = registration().model_copy(
        update={
            "config": agent_config().model_copy(
                update={"model": agent_config().model.model_copy(update={"model_revision_id": "mdlr_missingmodel0001"})}
            )
        }
    )

    with pytest.raises(AgentError) as rejected:
        await agent_service.register_builtin(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            registration=invalid,
        )
    assert rejected.value.code == "agent_revision_create_failed"

    async with transaction(agent_sessions) as session:
        assert await session.get(AgentRecord, BUILTIN_AGENT_ID) is None


@pytest.mark.anyio
async def test_builtin_name_conflict_creates_no_partial_agent(
    agent_service: AgentService,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await agent_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="custom-name-conflict",
        request=CreateAgentRequest(name="Foundation Assistant", config=agent_config()),
    )

    with pytest.raises(AgentError) as rejected:
        await agent_service.register_builtin(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            registration=registration(),
        )
    assert rejected.value.code == "agent_name_conflict"

    async with transaction(agent_sessions) as session:
        assert await session.get(AgentRecord, BUILTIN_AGENT_ID) is None
