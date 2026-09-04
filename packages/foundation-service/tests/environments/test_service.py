from datetime import UTC, datetime, timedelta

import pytest
from a13n_service.environments.domain import (
    CreateProviderRequest,
    CreateTemplateRequest,
    CreateTemplateRevisionRequest,
    EnvironmentCommandRequest,
    EnvironmentStatus,
    NewEnvironmentSelection,
    RetentionPolicy,
    UpdateProviderRequest,
    retention_action,
)
from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.environments.models import EnvironmentRecord
from a13n_service.etags import resource_etag
from a13n_service.interactions.thread_creation import allocate_thread
from a13n_service.interactions.thread_domain import CreateThreadRequest
from a13n_service.storage import short_session
from pydantic import ValidationError

from .conftest import WORKSPACE_ID, actor

pytestmark = pytest.mark.anyio


async def create_recipe(service, path, *, preparation="on_run"):
    provider = await service.create_provider(
        actor=actor(), workspace_id=WORKSPACE_ID, request=CreateProviderRequest(type="a13n.direct-local", name="Local")
    )
    template = await service.create_template(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="recipe",
        request=CreateTemplateRequest(
            name="Workspace",
            provider_id=provider.id,
            configuration={"root": {"path": str(path)}},
            preparation=preparation,
            retention={"idle": {"stop_after": None, "delete_after": None}},
        ),
    )
    return provider, template


async def test_template_allocation_is_inert_and_revision_is_frozen(environment_service, environment_sessions, tmp_path):
    root = tmp_path / "absent"
    provider, template = await create_recipe(environment_service, root)
    selection = NewEnvironmentSelection(template_id=template.id)
    environment = await environment_service.create_environment(
        actor=actor(), workspace_id=WORKSPACE_ID, request=selection, idempotency_key="allocate"
    )
    assert environment.status == "unprepared" and environment.generation == 0
    assert environment.template_revision_id == template.current_revision_id
    assert not root.exists()
    assert (
        await environment_service.create_environment(
            actor=actor(), workspace_id=WORKSPACE_ID, request=selection, idempotency_key="allocate"
        )
        == environment
    )
    revision = await environment_service.create_revision(
        actor=actor(),
        template_id=template.id,
        request=CreateTemplateRevisionRequest(
            expected_version=1,
            provider_id=provider.id,
            configuration={"root": {"path": str(tmp_path / "other")}},
            preparation="on_use",
            retention={"idle": {"stop_after": None, "delete_after": None}},
        ),
    )
    assert revision.version == 2
    async with short_session(environment_sessions) as session:
        stored = await session.get(EnvironmentRecord, environment.id)
        assert stored.template_revision_id == template.current_revision_id != revision.id
        assert stored.state is None and stored.operation_id is None
    later = await environment_service.create_environment(
        actor=actor(), workspace_id=WORKSPACE_ID, request=selection, idempotency_key="later"
    )
    assert later.id != environment.id and later.template_revision_id == revision.id


async def test_empty_thread_allocates_only_metadata_and_distinguishes_null(environment_service, tmp_path):
    _, template = await create_recipe(environment_service, tmp_path / "absent", preparation="on_use")
    body = CreateThreadRequest(environment=NewEnvironmentSelection(template_id=template.id))
    thread = await allocate_thread(
        environment_service, actor=actor(), workspace_id=WORKSPACE_ID, body=body, idempotency_key="thread"
    )
    assert thread.current_run_id is None and thread.head_run_id is None and thread.default_environment_id
    assert (
        await allocate_thread(
            environment_service, actor=actor(), workspace_id=WORKSPACE_ID, body=body, idempotency_key="thread"
        )
        == thread
    )
    assert not (tmp_path / "absent").exists()
    await allocate_thread(
        environment_service,
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        body=CreateThreadRequest(),
        idempotency_key="null-distinction",
    )
    with pytest.raises(EnvironmentManagementError, match="different request"):
        await allocate_thread(
            environment_service,
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            body=CreateThreadRequest(environment=None),
            idempotency_key="null-distinction",
        )


async def test_provider_disable_blocks_new_allocation(environment_service, tmp_path):
    provider, template = await create_recipe(environment_service, tmp_path)
    await environment_service.update_provider(
        actor=actor(),
        provider_id=provider.id,
        request=UpdateProviderRequest(enabled=False),
        if_match=resource_etag(provider.id, provider.updated_at),
    )
    with pytest.raises(EnvironmentManagementError, match="disabled"):
        await environment_service.create_environment(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            request=NewEnvironmentSelection(template_id=template.id),
            idempotency_key="disabled",
        )


async def test_local_provider_rejects_destructive_retention(environment_service, tmp_path):
    provider = await environment_service.create_provider(
        actor=actor(), workspace_id=WORKSPACE_ID, request=CreateProviderRequest(type="a13n.direct-local", name="Local")
    )
    with pytest.raises(EnvironmentManagementError, match="support stop"):
        await environment_service.create_template(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="unsupported",
            request=CreateTemplateRequest(
                name="Bad",
                provider_id=provider.id,
                configuration={"root": {"path": str(tmp_path)}},
                retention={"idle": {"stop_after": 10, "delete_after": None}},
            ),
        )


@pytest.mark.parametrize("condition,stop", [("idle", 600), ("waiting_approval", 3600)])
async def test_stop_never_resets_deletion_deadline(condition, stop):
    policy = RetentionPolicy.model_validate(
        {"idle": {"stop_after": 600, "delete_after": 604800}, "waiting_approval": {"stop_after": 3600}}
    )
    restored = RetentionPolicy.model_validate_json(policy.model_dump_json())
    assert restored.window(condition).delete_after == 604800
    start = datetime(2026, 9, 1, tzinfo=UTC)
    assert (
        retention_action(
            restored,
            condition=condition,
            since=start,
            status=EnvironmentStatus.running,
            now=start + timedelta(seconds=stop),
        )
        == "stop"
    )
    assert (
        retention_action(
            restored, condition=condition, since=start, status=EnvironmentStatus.stopped, now=start + timedelta(days=7)
        )
        == "delete"
    )
    assert (
        retention_action(
            restored, condition="active", since=start, status=EnvironmentStatus.running, now=start + timedelta(days=8)
        )
        is None
    )


async def test_explicit_null_disables_approval_action_and_invalid_deadlines_fail():
    policy = RetentionPolicy.model_validate(
        {"idle": {"stop_after": 60, "delete_after": 120}, "waiting_approval": {"stop_after": None}}
    )
    restored = RetentionPolicy.model_validate_json(policy.model_dump_json())
    assert restored.window("waiting_approval").stop_after is None
    assert restored.window("waiting_approval").delete_after == 120
    with pytest.raises(ValidationError):
        RetentionPolicy.model_validate(
            {"idle": {"stop_after": 60, "delete_after": 120}, "waiting_approval": {"stop_after": 180}}
        )


async def test_manual_command_is_a_durable_idempotent_receipt(environment_service, environment_sessions):
    provider = await environment_service.create_provider(
        actor=actor(), workspace_id=WORKSPACE_ID, request=CreateProviderRequest(type="a13n.docker", name="Docker")
    )
    template = await environment_service.create_template(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="docker",
        request=CreateTemplateRequest(
            name="Docker",
            provider_id=provider.id,
            configuration={},
            retention={"idle": {"stop_after": 600, "delete_after": 604800}},
        ),
    )
    environment = await environment_service.create_environment(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=NewEnvironmentSelection(template_id=template.id),
        idempotency_key="env",
    )
    request = EnvironmentCommandRequest(action="delete")
    command = await environment_service.request_command(
        actor=actor(), environment_id=environment.id, request=request, idempotency_key="delete"
    )
    assert command.status == "pending"
    assert (
        await environment_service.request_command(
            actor=actor(), environment_id=environment.id, request=request, idempotency_key="delete"
        )
        == command
    )
    async with short_session(environment_sessions) as session:
        stored = await session.get(EnvironmentRecord, environment.id)
        assert stored.operation_id == command.id and stored.operation_action == "delete"
    with pytest.raises(EnvironmentManagementError, match="pending work"):
        await environment_service.request_command(
            actor=actor(),
            environment_id=environment.id,
            request=EnvironmentCommandRequest(action="stop"),
            idempotency_key="stop",
        )


async def test_provider_credential_uses_owned_encrypted_bundle(
    environment_service, environment_sessions, tmp_path, monkeypatch, protector
):
    from a13n_environment_provider import DirectLocalEnvironmentProvider
    from a13n_service.environments.domain import ReplaceCredentialRequest
    from a13n_service.environments.models import EnvironmentProviderRecord
    from pydantic import BaseModel, ConfigDict

    class Credential(BaseModel):
        model_config = ConfigDict(extra="forbid")
        token: str

    monkeypatch.setattr(DirectLocalEnvironmentProvider, "credential_model", Credential)
    provider = await environment_service.create_provider(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateProviderRequest(type="a13n.direct-local", name="Owned", credential={"token": "initial-token"}),
    )
    assert provider.credential_configured and "initial-token" not in provider.model_dump_json()
    async with short_session(environment_sessions) as session:
        stored = await session.get(EnvironmentProviderRecord, provider.id)
        assert b"initial-token" not in stored.ciphertext
        assert Credential.model_validate_json(stored.credential_snapshot().decrypt(protector)).token == "initial-token"
        generation = stored.credential_generation
    changed = await environment_service.replace_credential(
        actor=actor(),
        provider_id=provider.id,
        if_match=resource_etag(provider.id, provider.updated_at),
        request=ReplaceCredentialRequest(credential={"token": "rotated-token"}),
    )
    async with short_session(environment_sessions) as session:
        stored = await session.get(EnvironmentProviderRecord, provider.id)
        assert stored.credential_generation == generation + 1
        assert Credential.model_validate_json(stored.credential_snapshot().decrypt(protector)).token == "rotated-token"
    assert "rotated-token" not in changed.model_dump_json()


async def test_collection_cursors_cannot_cross_resource_scope(environment_service, tmp_path):
    await create_recipe(environment_service, tmp_path)
    await environment_service.create_provider(
        actor=actor(), workspace_id=WORKSPACE_ID, request=CreateProviderRequest(type="a13n.direct-local", name="Second")
    )
    first = await environment_service.list_providers(actor=actor(), workspace_id=WORKSPACE_ID, limit=1)
    assert first.next_cursor
    next_page = await environment_service.list_providers(
        actor=actor(), workspace_id=WORKSPACE_ID, limit=1, cursor=first.next_cursor
    )
    assert next_page.items[0].id != first.items[0].id
    with pytest.raises(EnvironmentManagementError, match="another collection"):
        await environment_service.list_templates(actor=actor(), workspace_id=WORKSPACE_ID, cursor=first.next_cursor)
