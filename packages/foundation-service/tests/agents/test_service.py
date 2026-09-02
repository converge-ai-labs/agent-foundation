from __future__ import annotations

import pytest
from a13n_service.agents.domain import (
    CreateAgentRequest,
    CreateAgentRevisionRequest,
    DuplicateAgentRequest,
    RestoreAgentRevisionRequest,
    UpdateAgentRequest,
)
from a13n_service.agents.errors import AgentError
from a13n_service.agents.service import AgentService
from a13n_service.etags import resource_etag

from .conftest import WORKSPACE_ID, actor, agent_config


@pytest.mark.anyio
async def test_create_is_atomic_idempotent_and_starts_at_v1(agent_service: AgentService) -> None:
    request = CreateAgentRequest(name="Support", config=agent_config())

    created = await agent_service.create(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="create-support", request=request
    )
    replay = await agent_service.create(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="create-support", request=request
    )

    assert replay == created
    assert created.agent.version == created.revision.version == 1
    assert created.agent.current_revision_id == created.revision.id
    assert created.revision.config == request.config
    assert len(created.revision.runtime_lock_digest) == 64
    assert created.revision.connector_tools == ()
    assert created.revision.mcp_tools == ()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("config_field", "selection", "reason"),
    [
        (
            "connector_tools",
            {"orders": {"connector_connection_id": "cconn_1234567890abcdef"}},
            "connector_tool_resolution_unavailable",
        ),
        (
            "mcp_tools",
            {"docs": {"mcp_connection_id": "mcpc_1234567890abcdef"}},
            "mcp_tool_resolution_unavailable",
        ),
    ],
)
async def test_agent_creation_fails_closed_until_connectivity_resolution_is_available(
    agent_service: AgentService,
    config_field: str,
    selection: dict[str, object],
    reason: str,
) -> None:
    config = agent_config(**{config_field: selection})

    with pytest.raises(AgentError) as rejected:
        await agent_service.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key=f"create-{config_field}",
            request=CreateAgentRequest(name=f"Agent {config_field}", config=config),
        )

    assert rejected.value.code == "agent_revision_create_failed"
    assert rejected.value.details == {"reason": reason, "path": config_field}


@pytest.mark.anyio
async def test_revision_noop_new_revision_and_restore_follow_one_lineage(
    agent_service: AgentService,
) -> None:
    created = await agent_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-lineage",
        request=CreateAgentRequest(name="Lineage", config=agent_config()),
    )

    noop = await agent_service.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="noop-lineage",
        request=CreateAgentRevisionRequest(expected_version=1, config=agent_config()),
    )
    second = await agent_service.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="second-lineage",
        request=CreateAgentRevisionRequest(
            expected_version=1,
            config=agent_config(instructions="Analyze carefully."),
        ),
    )
    restored = await agent_service.restore_revision(
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
    revisions = await agent_service.list_revisions(actor=actor(), agent_id=created.agent.id, limit=10, cursor=None)
    assert [item.version for item in revisions.items] == [3, 2, 1]


@pytest.mark.anyio
async def test_revision_create_rejects_stale_head_version(agent_service: AgentService) -> None:
    created = await agent_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-conflict",
        request=CreateAgentRequest(name="Conflict", config=agent_config()),
    )
    await agent_service.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="advance-conflict",
        request=CreateAgentRevisionRequest(
            expected_version=1,
            config=agent_config(instructions="Changed"),
        ),
    )

    with pytest.raises(AgentError) as stale:
        await agent_service.create_revision(
            actor=actor(),
            agent_id=created.agent.id,
            idempotency_key="stale-conflict",
            request=CreateAgentRevisionRequest(
                expected_version=1,
                config=agent_config(instructions="Stale"),
            ),
        )
    assert stale.value.status_code == 409
    assert stale.value.details == {"current_version": 2}


@pytest.mark.anyio
async def test_metadata_and_lifecycle_use_etag_without_incrementing_version(
    agent_service: AgentService,
) -> None:
    created = await agent_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-metadata",
        request=CreateAgentRequest(name="Metadata", config=agent_config()),
    )
    etag = resource_etag(created.agent.id, created.agent.updated_at)

    updated = await agent_service.patch_metadata(
        actor=actor(),
        agent_id=created.agent.id,
        if_match=etag,
        request=UpdateAgentRequest(name="Renamed"),
    )
    with pytest.raises(AgentError) as stale:
        await agent_service.patch_metadata(
            actor=actor(),
            agent_id=created.agent.id,
            if_match='"stale"',
            request=UpdateAgentRequest(name="Rejected"),
        )
    assert stale.value.status_code == 412
    disabled = await agent_service.change_lifecycle(
        actor=actor(),
        agent_id=created.agent.id,
        action="disable",
        idempotency_key="disable-metadata",
        if_match=resource_etag(updated.id, updated.updated_at),
    )
    archived = await agent_service.change_lifecycle(
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
async def test_duplicate_copies_exact_current_revision_as_new_v1(agent_service: AgentService) -> None:
    created = await agent_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-duplicate",
        request=CreateAgentRequest(name="Original", config=agent_config()),
    )
    second = await agent_service.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="advance-duplicate",
        request=CreateAgentRevisionRequest(
            expected_version=1,
            config=agent_config(instructions="Current"),
        ),
    )

    duplicate = await agent_service.duplicate(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="duplicate",
        request=DuplicateAgentRequest(expected_version=2, name="Copy"),
    )
    duplicate_revision = await agent_service.get_revision(actor=actor(), revision_id=duplicate.current_revision_id)

    assert duplicate.version == duplicate_revision.version == 1
    assert duplicate.duplicated_from_revision_id == second.revision.id
    assert duplicate_revision.source_revision_id == second.revision.id
    assert duplicate_revision.config.instructions == "Current"


@pytest.mark.anyio
async def test_list_filters_enabled_and_archived_axes(agent_service: AgentService) -> None:
    created = await agent_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-list",
        request=CreateAgentRequest(name="Listed", config=agent_config()),
    )
    disabled = await agent_service.change_lifecycle(
        actor=actor(),
        agent_id=created.agent.id,
        action="disable",
        idempotency_key="disable-list",
        if_match=resource_etag(created.agent.id, created.agent.updated_at),
    )

    page = await agent_service.list(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        limit=10,
        cursor=None,
        enabled=False,
        source=None,
        include_archived=False,
    )
    assert page.items == (disabled,)
