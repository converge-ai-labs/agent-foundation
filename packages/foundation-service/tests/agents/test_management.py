from __future__ import annotations

from datetime import timedelta

import pytest
from a13n_service.agents.application import AgentManagement
from a13n_service.agents.domain import (
    CreateAgentRequest,
    CreateAgentRevisionRequest,
    DuplicateAgentRequest,
    RestoreAgentRevisionRequest,
    UpdateAgentRequest,
    canonical_digest,
)
from a13n_service.agents.errors import AgentError
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.etags import resource_etag
from a13n_service.http_errors import application_error_status
from a13n_service.storage import transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, WORKSPACE_ID, actor, agent_config


@pytest.mark.anyio
async def test_create_is_atomic_idempotent_and_starts_at_v1(agent_management: AgentManagement) -> None:
    request = CreateAgentRequest(name="Support", config=agent_config())

    created = await agent_management.commands.create(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="create-support", request=request
    )
    replay = await agent_management.commands.create(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="create-support", request=request
    )

    assert replay == created
    assert created.agent.version == created.revision.version == 1
    assert created.agent.current_revision_id == created.revision.id
    assert created.revision.config == request.config
    assert created.revision.config_digest == canonical_digest(request.config)
    assert len(created.revision.runtime_lock_digest) == 64
    assert created.revision.connector_tools == ()
    assert created.revision.mcp_tools == ()


@pytest.mark.anyio
async def test_expired_agent_evidence_allows_reusing_the_key(
    agent_management: AgentManagement,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    first = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="expired-agent-key",
        request=CreateAgentRequest(name="First", config=agent_config()),
    )
    async with transaction(agent_sessions) as session:
        evidence = await session.scalar(
            select(IdempotencyEvidenceRecord).where(IdempotencyEvidenceRecord.operation == "agent.create")
        )
        assert evidence is not None
        evidence.created_at = NOW - timedelta(hours=24)
        evidence.expires_at = NOW

    second = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="expired-agent-key",
        request=CreateAgentRequest(name="Second", config=agent_config(instructions="Second")),
    )

    assert second.agent.id != first.agent.id


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("config_field", "selection", "reason"),
    [
        (
            "connector_tools",
            ({"connector_connection_id": "cconn_1234567890abcdef"},),
            "connector_tool_resolution_unavailable",
        ),
        (
            "mcp_tools",
            ({"mcp_connection_id": "mcpc_1234567890abcdef"},),
            "mcp_tool_resolution_unavailable",
        ),
    ],
)
async def test_agent_creation_fails_closed_until_connectivity_resolution_is_available(
    agent_management: AgentManagement,
    config_field: str,
    selection: dict[str, object],
    reason: str,
) -> None:
    config = agent_config(**{config_field: selection})

    with pytest.raises(AgentError) as rejected:
        await agent_management.commands.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key=f"create-{config_field}",
            request=CreateAgentRequest(name=f"Agent {config_field}", config=config),
        )

    assert rejected.value.code == "agent_revision_create_failed"
    assert rejected.value.details == {"reason": reason, "path": config_field}


@pytest.mark.anyio
async def test_revision_noop_new_revision_and_restore_follow_one_lineage(
    agent_management: AgentManagement,
) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-lineage",
        request=CreateAgentRequest(name="Lineage", config=agent_config()),
    )

    noop = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="noop-lineage",
        request=CreateAgentRevisionRequest(expected_version=1, config=agent_config()),
    )
    second = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="second-lineage",
        request=CreateAgentRevisionRequest(
            expected_version=1,
            config=agent_config(instructions="Analyze carefully."),
        ),
    )
    restored = await agent_management.revisions.restore_revision(
        actor=actor(),
        agent_id=created.agent.id,
        revision_id=created.revision.id,
        idempotency_key="restore-lineage",
        request=RestoreAgentRevisionRequest(expected_version=2),
    )

    assert noop.revision.id == created.revision.id
    assert noop.agent.version == 1
    assert second.agent.version == second.revision.version == 2
    assert restored.agent.version == restored.revision.version == 3
    assert restored.revision.source_revision_id == created.revision.id
    assert restored.revision.config == created.revision.config
    revisions = await agent_management.queries.list_revisions(
        actor=actor(), agent_id=created.agent.id, limit=10, cursor=None
    )
    assert [item.version for item in revisions.items] == [3, 2, 1]


@pytest.mark.anyio
async def test_revision_create_rejects_stale_head_version(agent_management: AgentManagement) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-conflict",
        request=CreateAgentRequest(name="Conflict", config=agent_config()),
    )
    await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="advance-conflict",
        request=CreateAgentRevisionRequest(
            expected_version=1,
            config=agent_config(instructions="Changed"),
        ),
    )

    with pytest.raises(AgentError) as stale:
        await agent_management.revisions.create_revision(
            actor=actor(),
            agent_id=created.agent.id,
            idempotency_key="stale-conflict",
            request=CreateAgentRevisionRequest(
                expected_version=1,
                config=agent_config(instructions="Stale"),
            ),
        )
    assert application_error_status(stale.value) == 409
    assert stale.value.details == {"current_version": 2}


@pytest.mark.anyio
async def test_metadata_and_lifecycle_use_etag_without_incrementing_version(
    agent_management: AgentManagement,
) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-metadata",
        request=CreateAgentRequest(name="Metadata", config=agent_config()),
    )
    etag = resource_etag(created.agent.id, created.agent.updated_at)

    updated = await agent_management.commands.patch_metadata(
        actor=actor(),
        agent_id=created.agent.id,
        if_match=etag,
        request=UpdateAgentRequest(name="Renamed"),
    )
    with pytest.raises(AgentError) as stale:
        await agent_management.commands.patch_metadata(
            actor=actor(),
            agent_id=created.agent.id,
            if_match='"stale"',
            request=UpdateAgentRequest(name="Rejected"),
        )
    assert application_error_status(stale.value) == 412
    disabled = await agent_management.commands.change_lifecycle(
        actor=actor(),
        agent_id=created.agent.id,
        action="disable",
        idempotency_key="disable-metadata",
        if_match=resource_etag(updated.id, updated.updated_at),
    )
    archived = await agent_management.commands.change_lifecycle(
        actor=actor(),
        agent_id=created.agent.id,
        action="archive",
        idempotency_key="archive-metadata",
        if_match=resource_etag(disabled.id, disabled.updated_at),
    )

    assert updated.name == "Renamed"
    assert updated.version == disabled.version == archived.version == 1
    assert not disabled.enabled
    assert archived.archived_at is not None


@pytest.mark.anyio
async def test_duplicate_copies_exact_current_revision_as_new_v1(agent_management: AgentManagement) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-duplicate",
        request=CreateAgentRequest(name="Original", config=agent_config()),
    )
    second = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="advance-duplicate",
        request=CreateAgentRevisionRequest(
            expected_version=1,
            config=agent_config(instructions="Current"),
        ),
    )

    duplicate = await agent_management.duplication.duplicate(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="duplicate",
        request=DuplicateAgentRequest(expected_version=2, name="Copy"),
    )
    duplicate_revision = await agent_management.queries.get_revision(
        actor=actor(), revision_id=duplicate.current_revision_id
    )

    assert duplicate.version == duplicate_revision.version == 1
    assert duplicate.duplicated_from_revision_id == second.revision.id
    assert duplicate_revision.source_revision_id == second.revision.id
    assert duplicate_revision.config.instructions == "Current"


@pytest.mark.anyio
async def test_list_filters_enabled_and_archived_axes(agent_management: AgentManagement) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-list",
        request=CreateAgentRequest(name="Listed", config=agent_config()),
    )
    disabled = await agent_management.commands.change_lifecycle(
        actor=actor(),
        agent_id=created.agent.id,
        action="disable",
        idempotency_key="disable-list",
        if_match=resource_etag(created.agent.id, created.agent.updated_at),
    )

    page = await agent_management.queries.list(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        limit=10,
        cursor=None,
        enabled=False,
        source=None,
        include_archived=False,
    )
    assert page.items == (disabled,)
