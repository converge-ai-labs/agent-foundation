from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.etags import resource_etag
from a13n_service.iam.models import WorkspaceRecord
from a13n_service.plugins.domain import BuiltinPluginRegistration, PluginSource
from a13n_service.plugins.errors import PluginError
from a13n_service.plugins.models import PluginRecord, PluginVersionRecord
from a13n_service.plugins.service import PluginService
from a13n_service.storage import short_session, transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import ADMIN_ID, BUILDER_ID, NOW, ORG_ID, RecordingRuntimeDispatcher, actor, build_wheel, wheel_body

BUILTIN_PLUGIN_ID = "plg_builtinaudit0001"
BUILTIN_PLUGIN_VERSION_ID = "plgv_builtinauditv100"
SYSTEM_ACTOR_ID = "sa_pluginrelease0001"


def _builtin_registration(
    wheel: bytes,
    *,
    plugin_version_id: str = BUILTIN_PLUGIN_VERSION_ID,
    version: str = "1.0.0",
    plugin_key: str = "acme.audit",
    distribution_name: str = "acme-audit",
    top_level_package: str = "acme_audit",
    required: bool = False,
) -> BuiltinPluginRegistration:
    return BuiltinPluginRegistration(
        plugin_id=BUILTIN_PLUGIN_ID,
        plugin_version_id=plugin_version_id,
        system_actor_id=SYSTEM_ACTOR_ID,
        plugin_key=plugin_key,
        distribution_name=distribution_name,
        top_level_package=top_level_package,
        version=version,
        content_digest=hashlib.sha256(wheel).hexdigest(),
        required=required,
    )


async def _register_builtin(
    service: PluginService,
    wheel: bytes,
    *,
    registration: BuiltinPluginRegistration | None = None,
):
    return await service.register_builtin(
        registration=registration or _builtin_registration(wheel),
        body=wheel_body(wheel),
        content_length=len(wheel),
    )


async def _upload(
    service: PluginService,
    wheel: bytes,
    *,
    key: str,
    plugin_id: str | None = None,
):
    return await service.upload(
        actor=actor(),
        plugin_id=plugin_id,
        idempotency_key=key,
        filename="ignored-transport-name.whl",
        body=wheel_body(wheel),
        content_length=len(wheel),
    )


@pytest.mark.anyio
async def test_upload_creates_stable_plugin_and_immutable_versions(plugin_service: PluginService) -> None:
    first_wheel = build_wheel()
    first = await _upload(plugin_service, first_wheel, key="first")
    replay = await _upload(plugin_service, first_wheel, key="first")
    semantic_replay = await _upload(plugin_service, first_wheel, key="same-content")
    second = await _upload(
        plugin_service,
        build_wheel(version="1.1.0"),
        key="second",
        plugin_id=first.version.plugin_id,
    )

    assert first.created is True
    assert replay.created is False
    assert semantic_replay.created is False
    assert replay.version.id == first.version.id == semantic_replay.version.id
    assert second.created is True
    assert second.version.plugin_id == first.version.plugin_id
    assert second.version.version == "1.1.0"
    plugin = await plugin_service.get(actor=actor(), plugin_id=first.version.plugin_id)
    assert plugin.plugin_key == "acme.audit"
    assert plugin.distribution_name == "acme-audit"
    assert plugin.top_level_package == "acme_audit"
    assert plugin.active_version_id is None


@pytest.mark.anyio
async def test_plugin_upload_rejects_non_ascii_idempotency_keys(plugin_service: PluginService) -> None:
    with pytest.raises(PluginError) as caught:
        await _upload(plugin_service, build_wheel(), key="重试-🔁")
    assert caught.value.code == "invalid_request"


@pytest.mark.anyio
async def test_plugin_upload_replays_deployment_wide_key_across_workspaces(
    plugin_service: PluginService,
    plugin_sessions: async_sessionmaker[AsyncSession],
) -> None:
    second_workspace_id = "ws_second1234567890"
    async with transaction(plugin_sessions) as session:
        session.add(
            WorkspaceRecord(
                id=second_workspace_id,
                organization_id=ORG_ID,
                name="Second",
                normalized_name="second",
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )

    wheel = build_wheel()
    first = await _upload(plugin_service, wheel, key="deployment-wide-retry")
    replay = await plugin_service.upload(
        actor=actor(workspace_id=second_workspace_id),
        idempotency_key="deployment-wide-retry",
        filename="plugin.whl",
        body=wheel_body(wheel),
        content_length=len(wheel),
    )

    assert first.created is True
    assert replay.created is False
    assert replay.version.id == first.version.id


@pytest.mark.anyio
async def test_plugin_evidence_expires_at_the_exact_ttl_boundary(
    plugin_service: PluginService,
    plugin_sessions: async_sessionmaker[AsyncSession],
) -> None:
    key = "plugin-expiry-boundary"
    first = await _upload(plugin_service, build_wheel(), key=key)
    async with transaction(plugin_sessions) as session:
        evidence = await session.scalar(
            select(IdempotencyEvidenceRecord).where(IdempotencyEvidenceRecord.operation == "plugin.upload")
        )
        assert evidence is not None
        evidence.expires_at = NOW + timedelta(microseconds=1)

    different_wheel = build_wheel(
        plugin_key="acme.second",
        distribution_name="acme-second",
        package="acme_second",
    )
    with pytest.raises(PluginError) as conflict:
        await _upload(plugin_service, different_wheel, key=key)
    assert conflict.value.code == "idempotency_conflict"

    async with transaction(plugin_sessions) as session:
        evidence = await session.scalar(
            select(IdempotencyEvidenceRecord).where(IdempotencyEvidenceRecord.operation == "plugin.upload")
        )
        assert evidence is not None
        evidence.created_at = NOW - timedelta(hours=24)
        evidence.expires_at = NOW

    second = await _upload(plugin_service, different_wheel, key=key)

    assert second.created is True
    assert second.version.plugin_id != first.version.plugin_id


@pytest.mark.anyio
async def test_builtin_registration_is_verified_idempotent_and_upgradable(
    plugin_service: PluginService,
    plugin_sessions: async_sessionmaker[AsyncSession],
) -> None:
    first_wheel = build_wheel()
    first = await _register_builtin(plugin_service, first_wheel)
    replay = await _register_builtin(plugin_service, first_wheel)
    second_wheel = build_wheel(version="1.1.0")
    second = await _register_builtin(
        plugin_service,
        second_wheel,
        registration=_builtin_registration(
            second_wheel,
            plugin_version_id="plgv_builtinauditv110",
            version="1.1.0",
            required=True,
        ),
    )

    assert replay == first
    assert second.plugin_id == first.plugin_id == BUILTIN_PLUGIN_ID
    assert second.version == "1.1.0"
    plugin = await plugin_service.get(actor=actor(), plugin_id=BUILTIN_PLUGIN_ID)
    assert plugin.source is PluginSource.builtin
    assert plugin.active_version_id is None
    versions = await plugin_service.list_versions(
        actor=actor(),
        plugin_id=BUILTIN_PLUGIN_ID,
        limit=10,
        cursor=None,
    )
    assert {item.version for item in versions.items} == {"1.0.0", "1.1.0"}
    async with short_session(plugin_sessions) as session:
        record = await session.get(PluginRecord, BUILTIN_PLUGIN_ID)
        version = await session.get(PluginVersionRecord, second.id)
    assert record is not None and record.required is True
    assert record.created_by_type == "service_account"
    assert version is not None and version.created_by_id == SYSTEM_ACTOR_ID


@pytest.mark.anyio
async def test_builtin_registration_reserves_identity_and_cannot_be_archived(
    plugin_service: PluginService,
) -> None:
    wheel = build_wheel()
    version = await _register_builtin(plugin_service, wheel)
    plugin = await plugin_service.get(actor=actor(), plugin_id=version.plugin_id)

    with pytest.raises(PluginError) as upload_rejected:
        await _upload(
            plugin_service,
            build_wheel(version="2.0.0"),
            key="cannot-replace-builtin",
            plugin_id=version.plugin_id,
        )
    assert upload_rejected.value.code == "plugin_state_conflict"

    with pytest.raises(PluginError) as archive_rejected:
        await plugin_service.change_lifecycle(
            actor=actor(),
            plugin_id=version.plugin_id,
            action="archive",
            idempotency_key="cannot-archive-builtin",
            if_match=resource_etag(plugin.id, plugin.updated_at),
        )
    assert archive_rejected.value.code == "plugin_state_conflict"


@pytest.mark.anyio
async def test_builtin_registration_rejects_manifest_or_existing_identity_mismatch(
    plugin_service: PluginService,
) -> None:
    wheel = build_wheel()
    wrong_digest = _builtin_registration(wheel).model_copy(update={"content_digest": "0" * 64})
    with pytest.raises(PluginError) as digest_rejected:
        await _register_builtin(plugin_service, wheel, registration=wrong_digest)
    assert digest_rejected.value.code == "plugin_artifact_invalid"
    assert digest_rejected.value.details == {"reason": "builtin_manifest_digest_mismatch"}

    uploaded = await _upload(plugin_service, wheel, key="uploaded-owns-identity")
    assert uploaded.version.plugin_id != BUILTIN_PLUGIN_ID
    with pytest.raises(PluginError) as identity_rejected:
        await _register_builtin(plugin_service, wheel)
    assert identity_rejected.value.code == "plugin_identity_conflict"


@pytest.mark.anyio
async def test_upload_rejects_version_overwrite_and_identity_changes(plugin_service: PluginService) -> None:
    first = await _upload(plugin_service, build_wheel(), key="first")

    with pytest.raises(PluginError) as conflict:
        await _upload(
            plugin_service,
            build_wheel(requires_dist=("pydantic>=2",)),
            key="conflict",
            plugin_id=first.version.plugin_id,
        )
    assert conflict.value.code == "plugin_version_conflict"

    with pytest.raises(PluginError) as identity:
        await _upload(
            plugin_service,
            build_wheel(version="1.1", distribution_name="renamed-plugin"),
            key="identity",
            plugin_id=first.version.plugin_id,
        )
    assert identity.value.code == "plugin_identity_conflict"


@pytest.mark.anyio
async def test_plugin_lifecycle_hides_archived_and_blocks_upload(plugin_service: PluginService) -> None:
    uploaded = await _upload(plugin_service, build_wheel(), key="first")
    plugin_id = uploaded.version.plugin_id
    uploaded_plugin = await plugin_service.get(actor=actor(), plugin_id=plugin_id)

    archived = await plugin_service.change_lifecycle(
        actor=actor(),
        plugin_id=plugin_id,
        action="archive",
        idempotency_key="archive",
        if_match=resource_etag(plugin_id, uploaded_plugin.updated_at),
    )
    default_page = await plugin_service.list(
        actor=actor(),
        limit=50,
        cursor=None,
        source=None,
        include_archived=False,
    )
    assert archived.archived_at is not None
    assert default_page.items == ()
    with pytest.raises(PluginError) as rejected:
        await _upload(plugin_service, build_wheel(version="2.0"), key="blocked", plugin_id=plugin_id)
    assert rejected.value.code == "plugin_state_conflict"

    restored = await plugin_service.change_lifecycle(
        actor=actor(),
        plugin_id=plugin_id,
        action="unarchive",
        idempotency_key="unarchive",
        if_match=resource_etag(plugin_id, archived.updated_at),
    )
    assert restored.archived_at is None


@pytest.mark.anyio
async def test_plugin_collections_are_complete_and_paginated(plugin_service: PluginService) -> None:
    first = await _upload(
        plugin_service, build_wheel(plugin_key="acme.one", distribution_name="acme-one", package="acme_one"), key="one"
    )
    await _upload(
        plugin_service, build_wheel(plugin_key="acme.two", distribution_name="acme-two", package="acme_two"), key="two"
    )
    await _upload(
        plugin_service,
        build_wheel(version="1.1", plugin_key="acme.one", distribution_name="acme-one", package="acme_one"),
        key="one-next",
        plugin_id=first.version.plugin_id,
    )

    page = await plugin_service.list(
        actor=actor(),
        limit=1,
        cursor=None,
        source=None,
        include_archived=False,
    )
    assert len(page.items) == 1
    assert page.next_cursor is not None
    versions = await plugin_service.list_versions(
        actor=actor(), plugin_id=first.version.plugin_id, limit=10, cursor=None
    )
    assert {item.version for item in versions.items} == {"1.0.0", "1.1"}
    assert all(item.artifact_ref.startswith("plugins/artifacts/v1/sha256/") for item in versions.items)


@pytest.mark.anyio
async def test_workspace_builder_can_read_but_cannot_upload(plugin_service: PluginService) -> None:
    uploaded = await _upload(plugin_service, build_wheel(), key="first")
    assert (
        await plugin_service.get(actor=actor(BUILDER_ID), plugin_id=uploaded.version.plugin_id)
    ).id == uploaded.version.plugin_id

    wheel = build_wheel(version="2.0")
    with pytest.raises(PluginError) as rejected:
        await plugin_service.upload(
            actor=actor(BUILDER_ID),
            plugin_id=uploaded.version.plugin_id,
            idempotency_key="builder-upload",
            filename="plugin.whl",
            body=wheel_body(wheel),
            content_length=len(wheel),
        )
    assert rejected.value.code == "forbidden"
    assert actor(ADMIN_ID).principal.principal_id == ADMIN_ID


@pytest.mark.anyio
async def test_on_demand_runtime_commands_fail_without_creating_receipts(plugin_service: PluginService) -> None:
    with pytest.raises(PluginError) as activate:
        await plugin_service.activate(
            actor=actor(),
            plugin_version_id="plgv_missing1234567890",
            idempotency_key="activate-on-demand",
        )
    with pytest.raises(PluginError) as deactivate:
        await plugin_service.deactivate(
            actor=actor(),
            plugin_id="plg_missing1234567890",
            idempotency_key="deactivate-on-demand",
        )
    with pytest.raises(PluginError) as receipt:
        await plugin_service.get_operation(actor=actor(), operation_id="op_missing1234567890")

    assert activate.value.code == "plugin_runtime_mode_unsupported"
    assert deactivate.value.code == "plugin_runtime_mode_unsupported"
    assert receipt.value.code == "plugin_operation_not_found"


@pytest.mark.anyio
async def test_runner_commands_fail_closed_without_staging_dispatcher(
    runner_plugin_service: PluginService,
) -> None:
    with pytest.raises(PluginError) as activate:
        await runner_plugin_service.activate(
            actor=actor(),
            plugin_version_id="plgv_missing1234567890",
            idempotency_key="activate-without-dispatcher",
        )
    with pytest.raises(PluginError) as deactivate:
        await runner_plugin_service.deactivate(
            actor=actor(),
            plugin_id="plg_missing1234567890",
            idempotency_key="deactivate-without-dispatcher",
        )
    with pytest.raises(PluginError) as receipt:
        await runner_plugin_service.get_operation(actor=actor(), operation_id="op_missing1234567890")

    assert activate.value.code == "plugin_runtime_control_unavailable"
    assert deactivate.value.code == "plugin_runtime_control_unavailable"
    assert receipt.value.code == "plugin_runtime_control_unavailable"


@pytest.mark.anyio
async def test_runner_commands_dispatch_authorized_snapshots_and_return_same_receipt(
    runner_plugin_service_with_dispatcher: tuple[PluginService, RecordingRuntimeDispatcher],
) -> None:
    service, dispatcher = runner_plugin_service_with_dispatcher
    uploaded = await _upload(service, build_wheel(), key="upload-for-runtime-command")

    activated = await service.activate(
        actor=actor(),
        plugin_version_id=uploaded.version.id,
        idempotency_key="activate-version",
    )
    assert activated.status == "running"
    assert dispatcher.calls == [("activate", uploaded.version.id)]
    assert await service.get_operation(actor=actor(), operation_id=activated.operation_id) == activated

    deactivated = await service.deactivate(
        actor=actor(),
        plugin_id=uploaded.version.plugin_id,
        idempotency_key="deactivate-plugin",
    )
    assert deactivated.status == "running"
    assert dispatcher.calls == [
        ("activate", uploaded.version.id),
        ("deactivate", uploaded.version.plugin_id),
    ]
    assert await service.get_operation(actor=actor(), operation_id=deactivated.operation_id) == deactivated

    with pytest.raises(PluginError) as forbidden:
        await service.activate(
            actor=actor(BUILDER_ID),
            plugin_version_id=uploaded.version.id,
            idempotency_key="builder-cannot-activate",
        )
    assert forbidden.value.code == "forbidden"
    assert len(dispatcher.calls) == 2
