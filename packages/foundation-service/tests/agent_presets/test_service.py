from __future__ import annotations

import pytest
from a13n_service.agent_presets.domain import (
    AgentPresetCommandRequest,
    CreateAgentPresetRequest,
    DuplicateAgentPresetRequest,
    PatchAgentPresetRequest,
    ReplaceAgentPresetConfigRequest,
    SetDefaultAgentPresetRevisionRequest,
)
from a13n_service.agent_presets.errors import AgentPresetError
from a13n_service.agent_presets.invocation_resolution import AgentPresetInvocationResolver
from a13n_service.agent_presets.models import AgentPresetRecord
from a13n_service.agent_presets.service import AgentPresetService
from a13n_service.iam.models import RoleBindingRecord, UserRecord
from a13n_service.storage import transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import (
    DIRECT_USER_ID,
    NOW,
    ORG_ID,
    USER_ID,
    WORKSPACE_ID,
    actor,
    create_default_revision,
    preset_config,
)


@pytest.mark.anyio
async def test_create_revision_is_independent_from_default_selection(
    agent_preset_service: AgentPresetService,
) -> None:
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
    assert created.default_revision_id is None
    assert created.config_changed_since_revision

    revision_result = await agent_preset_service.create_revision(
        actor=actor(),
        preset_id=created.id,
        idempotency_key="create_revision-support-1",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    revision_replay = await agent_preset_service.create_revision(
        actor=actor(),
        preset_id=created.id,
        idempotency_key="create_revision-support-1",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )

    assert revision_replay == revision_result
    assert revision_result.revision.revision_number == 1
    assert revision_result.preset.default_revision_id is None
    assert not revision_result.preset.config_changed_since_revision
    assert revision_result.revision.resolved_model.execution.model_id == preset_config().model.model_config_id
    assert len(revision_result.revision.runtime_lock_digest) == 64
    assert revision_result.revision.connector_tools == ()
    assert revision_result.revision.mcp_tools == ()

    selected = await agent_preset_service.set_default_revision(
        actor=actor(),
        preset_id=created.id,
        idempotency_key="set-default-support-1",
        request=SetDefaultAgentPresetRevisionRequest(
            expected_resource_version=revision_result.preset.resource_version,
            revision_id=revision_result.revision.id,
        ),
    )
    selection_replay = await agent_preset_service.set_default_revision(
        actor=actor(),
        preset_id=created.id,
        idempotency_key="set-default-support-1",
        request=SetDefaultAgentPresetRevisionRequest(
            expected_resource_version=revision_result.preset.resource_version,
            revision_id=revision_result.revision.id,
        ),
    )
    assert selection_replay == selected
    assert selected.default_revision_id == revision_result.revision.id

    revisions = await agent_preset_service.list_revisions(actor=actor(), preset_id=created.id, limit=10, cursor=None)
    assert revisions.items == (revision_result.revision,)


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
async def test_revision_creation_fails_closed_until_connectivity_resolution_is_available(
    agent_preset_service: AgentPresetService,
    config_field: str,
    selection: dict[str, object],
    reason: str,
) -> None:
    config = preset_config(**{config_field: selection})
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key=f"create-{config_field}",
        request=CreateAgentPresetRequest(name=f"Preset {config_field}", config=config),
    )

    with pytest.raises(AgentPresetError) as rejected:
        await agent_preset_service.create_revision(
            actor=actor(),
            preset_id=preset.id,
            idempotency_key=f"create-revision-{config_field}",
            request=AgentPresetCommandRequest(expected_resource_version=1),
        )

    assert rejected.value.code == "preset_revision_create_failed"
    assert rejected.value.details == {"reason": reason, "path": config_field}


@pytest.mark.anyio
async def test_config_edit_revision_default_duplicate_and_lifecycle(
    agent_preset_service: AgentPresetService,
) -> None:
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-lifecycle",
        request=CreateAgentPresetRequest(name="Lifecycle", config=preset_config()),
    )
    first = await agent_preset_service.create_revision(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="create_revision-lifecycle-1",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    selected_first = await agent_preset_service.set_default_revision(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="set-default-lifecycle-1",
        request=SetDefaultAgentPresetRevisionRequest(
            expected_resource_version=first.preset.resource_version,
            revision_id=first.revision.id,
        ),
    )
    edited = await agent_preset_service.replace_config(
        actor=actor(),
        preset_id=preset.id,
        request=ReplaceAgentPresetConfigRequest(
            expected_resource_version=selected_first.resource_version,
            config=preset_config(instructions="Analyze carefully."),
        ),
    )
    assert edited.config_changed_since_revision

    second = await agent_preset_service.create_revision(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="create_revision-lifecycle-2",
        request=AgentPresetCommandRequest(expected_resource_version=edited.resource_version),
    )
    assert second.preset.default_revision_id == first.revision.id
    selected_second = await agent_preset_service.set_default_revision(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="set-default-lifecycle-2",
        request=SetDefaultAgentPresetRevisionRequest(
            expected_resource_version=second.preset.resource_version,
            revision_id=second.revision.id,
        ),
    )
    restored_first = await agent_preset_service.set_default_revision(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="restore-default-lifecycle-1",
        request=SetDefaultAgentPresetRevisionRequest(
            expected_resource_version=selected_second.resource_version,
            revision_id=first.revision.id,
        ),
    )
    assert second.revision.revision_number == 2
    assert restored_first.default_revision_id == first.revision.id
    assert restored_first.config.instructions == "Analyze carefully."

    duplicate = await agent_preset_service.duplicate(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="duplicate-lifecycle",
        request=DuplicateAgentPresetRequest(
            expected_resource_version=restored_first.resource_version,
            name="Lifecycle Copy",
        ),
    )
    duplicate_replay = await agent_preset_service.duplicate(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="duplicate-lifecycle",
        request=DuplicateAgentPresetRequest(
            expected_resource_version=restored_first.resource_version,
            name="Lifecycle Copy",
        ),
    )
    assert duplicate_replay == duplicate
    assert duplicate.duplicated_from_revision_id == first.revision.id
    assert duplicate.default_revision_id is not None
    duplicate_revisions = await agent_preset_service.list_revisions(
        actor=actor(), preset_id=duplicate.id, limit=10, cursor=None
    )
    assert duplicate_revisions.items[0].revision_number == 1
    assert duplicate.default_revision_id == duplicate_revisions.items[0].id

    disabled = await agent_preset_service.change_lifecycle(
        actor=actor(),
        preset_id=preset.id,
        action="disable",
        idempotency_key="disable-lifecycle",
        request=AgentPresetCommandRequest(expected_resource_version=restored_first.resource_version),
    )
    archived = await agent_preset_service.change_lifecycle(
        actor=actor(),
        preset_id=preset.id,
        action="archive",
        idempotency_key="archive-lifecycle",
        request=AgentPresetCommandRequest(expected_resource_version=disabled.resource_version),
    )
    unarchived = await agent_preset_service.change_lifecycle(
        actor=actor(),
        preset_id=preset.id,
        action="unarchive",
        idempotency_key="unarchive-lifecycle",
        request=AgentPresetCommandRequest(expected_resource_version=archived.resource_version),
    )
    assert disabled.lifecycle_state == "disabled"
    assert archived.lifecycle_state == "archived"
    assert unarchived.lifecycle_state == "disabled"


@pytest.mark.anyio
async def test_duplicate_accepts_a_disabled_source_with_default_revision(
    agent_preset_service: AgentPresetService,
) -> None:
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-disabled-duplicate-source",
        request=CreateAgentPresetRequest(name="Disabled Duplicate Source", config=preset_config()),
    )
    revision_result = await agent_preset_service.create_revision(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="create_revision-disabled-duplicate-source",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    selected = await agent_preset_service.set_default_revision(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="set-default-disabled-duplicate-source",
        request=SetDefaultAgentPresetRevisionRequest(
            expected_resource_version=revision_result.preset.resource_version,
            revision_id=revision_result.revision.id,
        ),
    )
    disabled = await agent_preset_service.change_lifecycle(
        actor=actor(),
        preset_id=preset.id,
        action="disable",
        idempotency_key="disable-duplicate-source",
        request=AgentPresetCommandRequest(expected_resource_version=selected.resource_version),
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
    assert duplicate.duplicated_from_revision_id == revision_result.revision.id


@pytest.mark.anyio
async def test_enable_without_default_keeps_exact_revision_invocation_available(
    agent_preset_service: AgentPresetService,
    agent_preset_invocation_resolver: AgentPresetInvocationResolver,
) -> None:
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-enable-without-default",
        request=CreateAgentPresetRequest(name="Enable Without Default", config=preset_config()),
    )
    revision_result = await agent_preset_service.create_revision(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="create-revision-enable-without-default",
        request=AgentPresetCommandRequest(expected_resource_version=preset.resource_version),
    )
    disabled = await agent_preset_service.change_lifecycle(
        actor=actor(),
        preset_id=preset.id,
        action="disable",
        idempotency_key="disable-without-default",
        request=AgentPresetCommandRequest(expected_resource_version=revision_result.preset.resource_version),
    )
    enabled = await agent_preset_service.change_lifecycle(
        actor=actor(),
        preset_id=preset.id,
        action="enable",
        idempotency_key="enable-without-default",
        request=AgentPresetCommandRequest(expected_resource_version=disabled.resource_version),
    )
    prepared = await agent_preset_invocation_resolver.prepare(
        actor=actor(),
        agent_preset_id=preset.id,
        agent_preset_revision_id=revision_result.revision.id,
    )

    assert enabled.lifecycle_state == "enabled"
    assert enabled.default_revision_id is None
    assert prepared.agent_preset_revision_id == revision_result.revision.id


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
async def test_disable_rejects_transitive_default_subagent_reference(
    agent_preset_service: AgentPresetService,
) -> None:
    child = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-transitive-child",
        request=CreateAgentPresetRequest(name="Child", config=preset_config()),
    )
    child_revision, selected_child = await create_default_revision(
        agent_preset_service,
        preset_id=child.id,
        expected_resource_version=1,
        key="transitive-child",
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
    middle_revision, _ = await create_default_revision(
        agent_preset_service,
        preset_id=middle.id,
        expected_resource_version=1,
        key="transitive-middle",
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
    await create_default_revision(
        agent_preset_service,
        preset_id=root.id,
        expected_resource_version=1,
        key="transitive-root",
    )

    with pytest.raises(AgentPresetError) as in_use:
        await agent_preset_service.change_lifecycle(
            actor=actor(),
            preset_id=child.id,
            action="disable",
            idempotency_key="disable-transitive-child",
            request=AgentPresetCommandRequest(expected_resource_version=selected_child.resource_version),
        )

    assert child_revision.revision.agent_preset_id == child.id
    assert middle_revision.revision.resolved_subagents[0].child_agent_preset_id == child.id
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
    await create_default_revision(
        agent_preset_service,
        preset_id=child.id,
        expected_resource_version=1,
        key="enable-child",
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
    await create_default_revision(
        agent_preset_service,
        preset_id=middle.id,
        expected_resource_version=1,
        key="enable-middle",
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
    _, selected_root = await create_default_revision(
        agent_preset_service,
        preset_id=root.id,
        expected_resource_version=1,
        key="enable-root",
    )
    disabled = await agent_preset_service.change_lifecycle(
        actor=actor(),
        preset_id=root.id,
        action="disable",
        idempotency_key="disable-enable-root",
        request=AgentPresetCommandRequest(expected_resource_version=selected_root.resource_version),
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
async def test_set_default_and_duplicate_reject_invalid_transitive_revision_graph(
    agent_preset_service: AgentPresetService,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    child = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-copy-validation-child",
        request=CreateAgentPresetRequest(name="Copy Validation Child", config=preset_config()),
    )
    await create_default_revision(
        agent_preset_service,
        preset_id=child.id,
        expected_resource_version=1,
        key="copy-validation-child",
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
    await create_default_revision(
        agent_preset_service,
        preset_id=middle.id,
        expected_resource_version=1,
        key="copy-validation-middle",
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
    first = await agent_preset_service.create_revision(
        actor=actor(),
        preset_id=root.id,
        idempotency_key="create_revision-copy-validation-root-1",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    second = await agent_preset_service.create_revision(
        actor=actor(),
        preset_id=root.id,
        idempotency_key="create_revision-copy-validation-root-2",
        request=AgentPresetCommandRequest(expected_resource_version=first.preset.resource_version),
    )
    selected_second = await agent_preset_service.set_default_revision(
        actor=actor(),
        preset_id=root.id,
        idempotency_key="set-default-copy-validation-root-2",
        request=SetDefaultAgentPresetRevisionRequest(
            expected_resource_version=second.preset.resource_version,
            revision_id=second.revision.id,
        ),
    )
    async with transaction(agent_preset_sessions) as session:
        child_record = await session.get(AgentPresetRecord, child.id)
        assert child_record is not None
        child_record.lifecycle_state = "disabled"

    with pytest.raises(AgentPresetError) as default_rejected:
        await agent_preset_service.set_default_revision(
            actor=actor(),
            preset_id=root.id,
            idempotency_key="set-default-invalid-copy-graph",
            request=SetDefaultAgentPresetRevisionRequest(
                expected_resource_version=selected_second.resource_version,
                revision_id=first.revision.id,
            ),
        )
    with pytest.raises(AgentPresetError) as duplicate_rejected:
        await agent_preset_service.duplicate(
            actor=actor(),
            preset_id=root.id,
            idempotency_key="duplicate-invalid-copy-graph",
            request=DuplicateAgentPresetRequest(
                expected_resource_version=selected_second.resource_version,
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
    assert default_rejected.value.code == "preset_disabled"
    assert duplicate_rejected.value.code == "preset_disabled"
    assert current.default_revision_id == second.revision.id
    assert current.resource_version == selected_second.resource_version
    assert len(revisions.items) == 2
