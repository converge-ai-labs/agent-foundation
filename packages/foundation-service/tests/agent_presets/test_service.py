from __future__ import annotations

import pytest
from a13n_service.agent_presets.domain import (
    AgentPresetCommandRequest,
    CreateAgentPresetRequest,
    DuplicateAgentPresetRequest,
    PatchAgentPresetRequest,
    ReplaceAgentPresetConfigRequest,
    RollbackAgentPresetRequest,
)
from a13n_service.agent_presets.errors import AgentPresetError
from a13n_service.agent_presets.models import AgentPresetRecord
from a13n_service.agent_presets.service import AgentPresetService
from a13n_service.iam.models import RoleBindingRecord, UserRecord
from a13n_service.storage import transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import DIRECT_USER_ID, NOW, ORG_ID, USER_ID, WORKSPACE_ID, actor, preset_config


@pytest.mark.anyio
async def test_create_publish_and_revision_history(agent_preset_service: AgentPresetService) -> None:
    created = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-support",
        request=CreateAgentPresetRequest(name="Support", config=preset_config()),
    )
    replay = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-support",
        request=CreateAgentPresetRequest(name="Support", config=preset_config()),
    )

    assert replay == created
    assert created.active_revision_id is None
    assert created.has_unpublished_changes

    published = await agent_preset_service.publish(
        actor=actor(),
        preset_id=created.id,
        idempotency_key="publish-support-1",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    publish_replay = await agent_preset_service.publish(
        actor=actor(),
        preset_id=created.id,
        idempotency_key="publish-support-1",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )

    assert publish_replay == published
    assert published.revision.revision_number == 1
    assert published.preset.active_revision_id == published.revision.id
    assert not published.preset.has_unpublished_changes
    assert published.revision.resolved_model.execution.model_id == preset_config().model.model_config_id
    assert len(published.revision.runtime_lock_digest) == 64

    revisions = await agent_preset_service.list_revisions(actor=actor(), preset_id=created.id, limit=10, cursor=None)
    assert revisions.items == (published.revision,)


@pytest.mark.anyio
async def test_config_edit_publish_rollback_duplicate_and_lifecycle(
    agent_preset_service: AgentPresetService,
) -> None:
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-lifecycle",
        request=CreateAgentPresetRequest(name="Lifecycle", config=preset_config()),
    )
    first = await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-lifecycle-1",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    edited = await agent_preset_service.replace_config(
        actor=actor(),
        preset_id=preset.id,
        request=ReplaceAgentPresetConfigRequest(
            expected_resource_version=2,
            config=preset_config(instructions="Analyze carefully."),
        ),
    )
    assert edited.has_unpublished_changes
    with pytest.raises(AgentPresetError) as unpublished:
        await agent_preset_service.rollback(
            actor=actor(),
            preset_id=preset.id,
            idempotency_key="rollback-too-soon",
            request=RollbackAgentPresetRequest(
                expected_resource_version=3,
                source_revision_id=first.revision.id,
            ),
        )
    assert unpublished.value.code == "config_has_unpublished_changes"

    second = await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-lifecycle-2",
        request=AgentPresetCommandRequest(expected_resource_version=3),
    )
    rolled_back = await agent_preset_service.rollback(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="rollback-lifecycle",
        request=RollbackAgentPresetRequest(
            expected_resource_version=4,
            source_revision_id=first.revision.id,
        ),
    )
    assert second.revision.revision_number == 2
    assert rolled_back.revision.revision_number == 3
    assert rolled_back.revision.source_revision_id == first.revision.id
    assert rolled_back.preset.config.instructions == "Be helpful."

    duplicate = await agent_preset_service.duplicate(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="duplicate-lifecycle",
        request=DuplicateAgentPresetRequest(
            expected_resource_version=5,
            name="Lifecycle Copy",
        ),
    )
    duplicate_replay = await agent_preset_service.duplicate(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="duplicate-lifecycle",
        request=DuplicateAgentPresetRequest(
            expected_resource_version=5,
            name="Lifecycle Copy",
        ),
    )
    assert duplicate_replay == duplicate
    assert duplicate.duplicated_from_revision_id == rolled_back.revision.id
    assert duplicate.active_revision_id is not None
    duplicate_revisions = await agent_preset_service.list_revisions(
        actor=actor(), preset_id=duplicate.id, limit=10, cursor=None
    )
    assert duplicate_revisions.items[0].revision_number == 1
    assert duplicate.active_revision_id == duplicate_revisions.items[0].id

    disabled = await agent_preset_service.change_lifecycle(
        actor=actor(),
        preset_id=preset.id,
        action="disable",
        idempotency_key="disable-lifecycle",
        request=AgentPresetCommandRequest(expected_resource_version=5),
    )
    archived = await agent_preset_service.change_lifecycle(
        actor=actor(),
        preset_id=preset.id,
        action="archive",
        idempotency_key="archive-lifecycle",
        request=AgentPresetCommandRequest(expected_resource_version=6),
    )
    unarchived = await agent_preset_service.change_lifecycle(
        actor=actor(),
        preset_id=preset.id,
        action="unarchive",
        idempotency_key="unarchive-lifecycle",
        request=AgentPresetCommandRequest(expected_resource_version=7),
    )
    assert disabled.lifecycle_state == "disabled"
    assert archived.lifecycle_state == "archived"
    assert unarchived.lifecycle_state == "disabled"


@pytest.mark.anyio
async def test_duplicate_accepts_a_disabled_published_source(
    agent_preset_service: AgentPresetService,
) -> None:
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-disabled-duplicate-source",
        request=CreateAgentPresetRequest(name="Disabled Duplicate Source", config=preset_config()),
    )
    published = await agent_preset_service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-disabled-duplicate-source",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    disabled = await agent_preset_service.change_lifecycle(
        actor=actor(),
        preset_id=preset.id,
        action="disable",
        idempotency_key="disable-duplicate-source",
        request=AgentPresetCommandRequest(expected_resource_version=published.preset.resource_version),
    )

    duplicate = await agent_preset_service.duplicate(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="duplicate-disabled-source",
        request=DuplicateAgentPresetRequest(
            expected_resource_version=disabled.resource_version,
            name="Disabled Source Copy",
        ),
    )

    assert duplicate.lifecycle_state == "enabled"
    assert duplicate.duplicated_from_revision_id == published.revision.id


@pytest.mark.anyio
async def test_optimistic_lock_and_idempotency_conflicts(agent_preset_service: AgentPresetService) -> None:
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-conflict",
        request=CreateAgentPresetRequest(name="Conflict", config=preset_config()),
    )
    with pytest.raises(AgentPresetError) as stale:
        await agent_preset_service.patch_metadata(
            actor=actor(),
            preset_id=preset.id,
            request=PatchAgentPresetRequest(expected_resource_version=2, name="New name"),
        )
    assert stale.value.code == "resource_version_conflict"

    with pytest.raises(AgentPresetError) as replay_conflict:
        await agent_preset_service.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="create-conflict",
            request=CreateAgentPresetRequest(name="Different", config=preset_config()),
        )
    assert replay_conflict.value.code == "idempotency_conflict"


@pytest.mark.anyio
async def test_list_presets_with_direct_agent_preset_visibility(
    agent_preset_service: AgentPresetService,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    visible = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-direct-visible",
        request=CreateAgentPresetRequest(name="Visible", config=preset_config()),
    )
    await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-direct-hidden",
        request=CreateAgentPresetRequest(name="Hidden", config=preset_config()),
    )
    async with transaction(agent_preset_sessions) as session:
        session.add_all(
            (
                UserRecord(
                    id=DIRECT_USER_ID,
                    email="direct@example.com",
                    normalized_email="direct@example.com",
                    name="Direct Viewer",
                    status="active",
                    email_verified_at=NOW,
                    version=1,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                RoleBindingRecord(
                    id="rb_orgdirect1234567",
                    organization_id=ORG_ID,
                    workspace_id=None,
                    principal_type="user",
                    principal_id=DIRECT_USER_ID,
                    resource_type="organization",
                    resource_id=ORG_ID,
                    role_key="member",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                RoleBindingRecord(
                    id="rb_apdirect12345678",
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    principal_type="user",
                    principal_id=DIRECT_USER_ID,
                    resource_type="agent_preset",
                    resource_id=visible.id,
                    role_key="viewer",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            )
        )

    result = await agent_preset_service.list(
        actor=actor(DIRECT_USER_ID),
        workspace_id=WORKSPACE_ID,
        limit=10,
        cursor=None,
        lifecycle_state=None,
        source=None,
        include_archived=False,
    )

    assert tuple(item.id for item in result.items) == (visible.id,)


@pytest.mark.anyio
async def test_disable_rejects_transitive_active_subagent_reference(
    agent_preset_service: AgentPresetService,
) -> None:
    child = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-transitive-child",
        request=CreateAgentPresetRequest(name="Child", config=preset_config()),
    )
    child_published = await agent_preset_service.publish(
        actor=actor(),
        preset_id=child.id,
        idempotency_key="publish-transitive-child",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    middle = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-transitive-middle",
        request=CreateAgentPresetRequest(
            name="Middle",
            config=preset_config(subagents={"child": {"agent_preset_id": child.id, "environment": {"mode": "none"}}}),
        ),
    )
    middle_published = await agent_preset_service.publish(
        actor=actor(),
        preset_id=middle.id,
        idempotency_key="publish-transitive-middle",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    root = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-transitive-root",
        request=CreateAgentPresetRequest(
            name="Root",
            config=preset_config(subagents={"middle": {"agent_preset_id": middle.id, "environment": {"mode": "none"}}}),
        ),
    )
    await agent_preset_service.publish(
        actor=actor(),
        preset_id=root.id,
        idempotency_key="publish-transitive-root",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )

    with pytest.raises(AgentPresetError) as in_use:
        await agent_preset_service.change_lifecycle(
            actor=actor(),
            preset_id=child.id,
            action="disable",
            idempotency_key="disable-transitive-child",
            request=AgentPresetCommandRequest(expected_resource_version=child_published.preset.resource_version),
        )

    assert middle_published.revision.resolved_subagents[0].child_agent_preset_id == child.id
    assert in_use.value.code == "preset_in_use"


@pytest.mark.anyio
async def test_enable_revalidates_every_retained_subagent_revision(
    agent_preset_service: AgentPresetService,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    child = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-enable-child",
        request=CreateAgentPresetRequest(name="Enable Child", config=preset_config()),
    )
    await agent_preset_service.publish(
        actor=actor(),
        preset_id=child.id,
        idempotency_key="publish-enable-child",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    middle = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-enable-middle",
        request=CreateAgentPresetRequest(
            name="Enable Middle",
            config=preset_config(subagents={"child": {"agent_preset_id": child.id, "environment": {"mode": "none"}}}),
        ),
    )
    await agent_preset_service.publish(
        actor=actor(),
        preset_id=middle.id,
        idempotency_key="publish-enable-middle",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    root = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-enable-root",
        request=CreateAgentPresetRequest(
            name="Enable Root",
            config=preset_config(subagents={"middle": {"agent_preset_id": middle.id, "environment": {"mode": "none"}}}),
        ),
    )
    published = await agent_preset_service.publish(
        actor=actor(),
        preset_id=root.id,
        idempotency_key="publish-enable-root",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    disabled = await agent_preset_service.change_lifecycle(
        actor=actor(),
        preset_id=root.id,
        action="disable",
        idempotency_key="disable-enable-root",
        request=AgentPresetCommandRequest(expected_resource_version=published.preset.resource_version),
    )
    async with transaction(agent_preset_sessions) as session:
        child_record = await session.get(AgentPresetRecord, child.id)
        assert child_record is not None
        child_record.lifecycle_state = "disabled"

    with pytest.raises(AgentPresetError) as rejected:
        await agent_preset_service.change_lifecycle(
            actor=actor(),
            preset_id=root.id,
            action="enable",
            idempotency_key="enable-root-with-disabled-descendant",
            request=AgentPresetCommandRequest(expected_resource_version=disabled.resource_version),
        )

    current = await agent_preset_service.get(actor=actor(), preset_id=root.id)
    assert rejected.value.code == "preset_disabled"
    assert current.lifecycle_state == "disabled"
    assert current.resource_version == disabled.resource_version


@pytest.mark.anyio
async def test_rollback_and_duplicate_reject_invalid_transitive_revision_graph(
    agent_preset_service: AgentPresetService,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    child = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-copy-validation-child",
        request=CreateAgentPresetRequest(name="Copy Validation Child", config=preset_config()),
    )
    await agent_preset_service.publish(
        actor=actor(),
        preset_id=child.id,
        idempotency_key="publish-copy-validation-child",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    middle = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-copy-validation-middle",
        request=CreateAgentPresetRequest(
            name="Copy Validation Middle",
            config=preset_config(subagents={"child": {"agent_preset_id": child.id, "environment": {"mode": "none"}}}),
        ),
    )
    await agent_preset_service.publish(
        actor=actor(),
        preset_id=middle.id,
        idempotency_key="publish-copy-validation-middle",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    root = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-copy-validation-root",
        request=CreateAgentPresetRequest(
            name="Copy Validation Root",
            config=preset_config(subagents={"middle": {"agent_preset_id": middle.id, "environment": {"mode": "none"}}}),
        ),
    )
    first = await agent_preset_service.publish(
        actor=actor(),
        preset_id=root.id,
        idempotency_key="publish-copy-validation-root-1",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    second = await agent_preset_service.publish(
        actor=actor(),
        preset_id=root.id,
        idempotency_key="publish-copy-validation-root-2",
        request=AgentPresetCommandRequest(expected_resource_version=first.preset.resource_version),
    )
    async with transaction(agent_preset_sessions) as session:
        child_record = await session.get(AgentPresetRecord, child.id)
        assert child_record is not None
        child_record.lifecycle_state = "disabled"

    with pytest.raises(AgentPresetError) as rollback_rejected:
        await agent_preset_service.rollback(
            actor=actor(),
            preset_id=root.id,
            idempotency_key="rollback-invalid-copy-graph",
            request=RollbackAgentPresetRequest(
                expected_resource_version=second.preset.resource_version,
                source_revision_id=first.revision.id,
            ),
        )
    with pytest.raises(AgentPresetError) as duplicate_rejected:
        await agent_preset_service.duplicate(
            actor=actor(),
            preset_id=root.id,
            idempotency_key="duplicate-invalid-copy-graph",
            request=DuplicateAgentPresetRequest(
                expected_resource_version=second.preset.resource_version,
                name="Invalid Graph Copy",
            ),
        )

    current = await agent_preset_service.get(actor=actor(), preset_id=root.id)
    revisions = await agent_preset_service.list_revisions(
        actor=actor(),
        preset_id=root.id,
        limit=10,
        cursor=None,
    )
    assert rollback_rejected.value.code == "preset_disabled"
    assert duplicate_rejected.value.code == "preset_disabled"
    assert current.active_revision_id == second.revision.id
    assert current.resource_version == second.preset.resource_version
    assert len(revisions.items) == 2
