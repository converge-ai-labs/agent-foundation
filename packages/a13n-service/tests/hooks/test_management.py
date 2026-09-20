from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.etags import resource_etag
from a13n_service.hooks import (
    CreateHookSubscriptionRequest,
    UpdateHookSubscriptionRequest,
    UpdateHookSubscriptionStateRequest,
    WebhookDestinationConfig,
)
from a13n_service.hooks.dispatcher import HookDispatcher
from a13n_service.hooks.errors import HookManagementError
from a13n_service.hooks.management import HookSubscriptionService
from a13n_service.hooks.models import HookSubscriptionRevisionRecord
from a13n_service.http_errors import application_error_status
from a13n_service.iam import AuthorizationError, WorkspaceAction, authorize_workspace
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.interactions.models import RunRecord
from a13n_service.storage import short_session, transaction
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.interactions.conftest import NOW, USER_ID, WORKSPACE_ID
from tests.lifecycle_support import test_lifecycle_writer

from .support import RUN_ID, SECRET_ID, hook_actor, seed_hook_actor_access, seed_run_and_secret

ENDPOINT = "https://93.184.216.34/hooks"


class _Clock:
    def __init__(self) -> None:
        self._ticks = 0

    def __call__(self):
        self._ticks += 1
        return NOW + timedelta(seconds=self._ticks)


def _request(
    *,
    endpoint_url: str = ENDPOINT,
    hook_names: tuple[str, ...] = ("run.accepted",),
) -> CreateHookSubscriptionRequest:
    return CreateHookSubscriptionRequest(
        hook_names=hook_names,
        run_id=RUN_ID,
        webhook=WebhookDestinationConfig(
            endpoint_url=endpoint_url,
            signing_secret_id=SECRET_ID,
        ),
    )


def _service(sessions: async_sessionmaker[AsyncSession]) -> HookSubscriptionService:
    return HookSubscriptionService(sessions, EndpointPolicy(), clock=_Clock())


async def _prepare(sessions: async_sessionmaker[AsyncSession]) -> HookSubscriptionService:
    await seed_run_and_secret(sessions)
    await seed_hook_actor_access(sessions)
    return _service(sessions)


@pytest.mark.anyio
async def test_managed_hook_crud_preserves_immutable_revisions_and_etags(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    service = await _prepare(hook_interaction_sessions)
    created = await service.create(actor=hook_actor(), workspace_id=WORKSPACE_ID, request=_request())
    original_etag = resource_etag(created.id, created.updated_at)

    fetched = await service.get(actor=hook_actor(), subscription_id=created.id)
    listed = await service.list(actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=1, cursor=None)
    assert fetched == created
    assert listed.items == (created,)
    assert listed.next_cursor is None

    same = await service.update_configuration(
        actor=hook_actor(),
        subscription_id=created.id,
        if_match=original_etag,
        request=UpdateHookSubscriptionRequest.model_validate(_request().model_dump()),
    )
    assert same.version == 1
    assert resource_etag(same.id, same.updated_at) == original_etag

    changed = await service.update_configuration(
        actor=hook_actor(),
        subscription_id=created.id,
        if_match=original_etag,
        request=UpdateHookSubscriptionRequest.model_validate(
            _request(endpoint_url="https://93.184.216.35/hooks").model_dump()
        ),
    )
    changed_etag = resource_etag(changed.id, changed.updated_at)
    assert changed.version == 2
    assert changed.current_revision.id != created.current_revision.id
    assert changed_etag != original_etag

    paused = await service.update_state(
        actor=hook_actor(),
        subscription_id=created.id,
        if_match=changed_etag,
        request=UpdateHookSubscriptionStateRequest(enabled=False),
    )
    paused_etag = resource_etag(paused.id, paused.updated_at)
    assert paused.enabled is False
    assert paused.version == 2

    with pytest.raises(HookManagementError, match="changed") as stale:
        await service.update_state(
            actor=hook_actor(),
            subscription_id=created.id,
            if_match=changed_etag,
            request=UpdateHookSubscriptionStateRequest(enabled=True),
        )
    assert application_error_status(stale.value) == 412

    async with short_session(hook_interaction_sessions) as database:
        revisions = await database.scalar(
            select(func.count(HookSubscriptionRevisionRecord.id)).where(
                HookSubscriptionRevisionRecord.hook_subscription_id == created.id
            )
        )
        original = await database.get(HookSubscriptionRevisionRecord, created.current_revision.id)
        assert revisions == 2
        assert original is not None and original.endpoint_url == ENDPOINT

    await service.delete(actor=hook_actor(), subscription_id=created.id, if_match=paused_etag)
    with pytest.raises(HookManagementError) as deleted:
        await service.get(actor=hook_actor(), subscription_id=created.id)
    assert deleted.value.code == "hook_subscription_not_found"


@pytest.mark.anyio
async def test_hook_collection_cursor_is_bound_and_runner_cannot_manage(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    service = await _prepare(hook_interaction_sessions)
    first = await service.create(actor=hook_actor(), workspace_id=WORKSPACE_ID, request=_request())
    second = await service.create(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        request=_request(hook_names=("run.running",)),
    )
    page_one = await service.list(actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=1, cursor=None)
    assert len(page_one.items) == 1
    assert page_one.next_cursor is not None
    page_two = await service.list(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        limit=1,
        cursor=page_one.next_cursor,
    )
    assert {page_one.items[0].id, page_two.items[0].id} == {first.id, second.id}

    async with transaction(hook_interaction_sessions) as database:
        binding = await database.scalar(
            select(RoleBindingRecord).where(
                RoleBindingRecord.principal_id == USER_ID,
                RoleBindingRecord.resource_type == "workspace",
            )
        )
        assert binding is not None
        binding.role_key = "runner"

    async with short_session(hook_interaction_sessions) as database:
        await authorize_workspace(
            database,
            actor=hook_actor(),
            workspace_id=WORKSPACE_ID,
            action=WorkspaceAction.hook_subscription_create,
        )
        with pytest.raises(AuthorizationError):
            await authorize_workspace(
                database,
                actor=hook_actor(),
                workspace_id=WORKSPACE_ID,
                action=WorkspaceAction.secrets_bind,
            )

    readable = await service.list(actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=10, cursor=None)
    assert len(readable.items) == 2
    with pytest.raises(HookManagementError) as denied:
        await service.create(
            actor=hook_actor(),
            workspace_id=WORKSPACE_ID,
            request=_request(endpoint_url="https://127.0.0.1/hooks"),
        )
    assert application_error_status(denied.value) == 404

    other_boundary = replace(
        hook_actor(),
        boundary_workspace_id="ws_9999999999999999",
    )
    with pytest.raises(HookManagementError) as invalid_cursor:
        await service.list(
            actor=other_boundary,
            workspace_id="ws_9999999999999999",
            limit=1,
            cursor=page_one.next_cursor,
        )
    assert invalid_cursor.value.code == "invalid_cursor"


@pytest.mark.anyio
async def test_hook_redrive_reuses_delivery_identity_and_original_revision(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    service = await _prepare(hook_interaction_sessions)
    created = await service.create(actor=hook_actor(), workspace_id=WORKSPACE_ID, request=_request())

    async with transaction(hook_interaction_sessions) as database:
        run = await database.get(RunRecord, RUN_ID)
        assert run is not None
        await test_lifecycle_writer().append_run_lifecycle(
            database,
            run,
            "run.accepted",
            mutation_id="mut_7171717171717171",
            occurred_at=NOW + timedelta(minutes=1),
            actor_type="user",
            actor_id=USER_ID,
        )
    await HookDispatcher(hook_interaction_sessions).scan()
    async with transaction(hook_interaction_sessions) as database:
        delivery = await database.scalar(select(OutboxRecord))
        assert delivery is not None
        delivery.status = "dead_lettered"
        delivery.attempt_count = 4
        delivery.dead_lettered_at = NOW + timedelta(minutes=2)
        delivery.updated_at = delivery.dead_lettered_at
        delivery_id = delivery.id
        original_revision_id = delivery.destination_ref

    changed = await service.update_configuration(
        actor=hook_actor(),
        subscription_id=created.id,
        if_match=resource_etag(created.id, created.updated_at),
        request=UpdateHookSubscriptionRequest.model_validate(
            _request(endpoint_url="https://93.184.216.35/hooks").model_dump()
        ),
    )
    assert changed.current_revision.id != original_revision_id

    await service.redrive(
        actor=hook_actor(),
        subscription_id=created.id,
        delivery_id=delivery_id,
    )
    async with short_session(hook_interaction_sessions) as database:
        redriven = await database.get(OutboxRecord, delivery_id)
        assert redriven is not None
        assert redriven.id == delivery_id
        assert redriven.destination_ref == original_revision_id
        assert redriven.status == "pending"
        assert redriven.attempt_count == 0
        assert redriven.dead_lettered_at is None


@pytest.mark.anyio
async def test_managed_hook_rejects_invalid_scope_destination_and_secret(
    hook_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    service = await _prepare(hook_interaction_sessions)
    with pytest.raises(HookManagementError) as scope:
        await service.create(
            actor=hook_actor(),
            workspace_id=WORKSPACE_ID,
            request=_request().model_copy(update={"run_id": "run_9999999999999999"}),
        )
    assert scope.value.code == "invalid_hook_scope"

    missing_secret = _request().model_copy(
        update={"webhook": _request().webhook.model_copy(update={"signing_secret_id": "sec_9999999999999999"})}
    )
    with pytest.raises(HookManagementError) as secret:
        await service.create(actor=hook_actor(), workspace_id=WORKSPACE_ID, request=missing_secret)
    assert secret.value.code == "hook_signing_secret_unavailable"

    with pytest.raises(HookManagementError) as endpoint:
        await service.create(
            actor=hook_actor(),
            workspace_id=WORKSPACE_ID,
            request=_request(endpoint_url="https://127.0.0.1/hooks"),
        )
    assert endpoint.value.code == "invalid_webhook_endpoint"


@pytest.mark.anyio
async def test_inline_configuration_is_immutable_but_manual_state_remains_manageable(hook_interaction_sessions):
    from a13n_service.hooks import InlineHookSubscriptionInput
    from a13n_service.hooks.models import HookSubscriptionRecord
    from a13n_service.hooks.persistence import create_inline_hook_subscription

    from tests.interactions.conftest import ORGANIZATION_ID, SESSION_ID, THREAD_ID

    sessions = hook_interaction_sessions
    await _prepare(sessions)
    service = HookSubscriptionService(sessions, EndpointPolicy(), clock=lambda: NOW)
    async with transaction(sessions) as database:
        head = await create_inline_hook_subscription(
            database,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            session_id=SESSION_ID,
            thread_id=THREAD_ID,
            run_id=RUN_ID,
            actor_type="user",
            actor_id=USER_ID,
            subscription=InlineHookSubscriptionInput(hook_names=("run.accepted",), webhook=_request().webhook),
            now=NOW,
        )
    created = await service.get(actor=hook_actor(), subscription_id=head.id)
    assert created.inline_run_id == RUN_ID and created.expired_at is None
    with pytest.raises(HookManagementError) as immutable:
        await service.update_configuration(
            actor=hook_actor(),
            subscription_id=head.id,
            if_match=resource_etag(head.id, created.updated_at),
            request=UpdateHookSubscriptionRequest(
                hook_names=created.current_revision.hook_names,
                webhook=created.current_revision.webhook,
                session_id=SESSION_ID,
                thread_id=THREAD_ID,
                run_id=RUN_ID,
            ),
        )
    assert immutable.value.code == "inline_hook_configuration_immutable"
    assert application_error_status(immutable.value) == 409
    async with transaction(sessions) as database:
        persisted = await database.get(HookSubscriptionRecord, head.id)
        persisted.expired_at = NOW + timedelta(seconds=1)
        persisted.updated_at = persisted.expired_at
    expired = await service.get(actor=hook_actor(), subscription_id=head.id)
    assert resource_etag(head.id, expired.updated_at) != resource_etag(head.id, created.updated_at)
    for enabled in (False, True):
        prior_etag = resource_etag(head.id, expired.updated_at)
        expired = await service.update_state(
            actor=hook_actor(),
            subscription_id=head.id,
            if_match=resource_etag(head.id, expired.updated_at),
            request=UpdateHookSubscriptionStateRequest(enabled=enabled),
        )
        assert resource_etag(head.id, expired.updated_at) != prior_etag
        assert expired.enabled is enabled and expired.expired_at == NOW + timedelta(seconds=1)
        assert expired.version == 1 and expired.current_revision == created.current_revision
    await service.delete(
        actor=hook_actor(), subscription_id=head.id, if_match=resource_etag(head.id, expired.updated_at)
    )
    async with short_session(sessions) as database:
        persisted = await database.get(HookSubscriptionRecord, head.id)
        assert persisted.deleted_at is not None and persisted.expired_at is not None
        assert await database.scalar(select(func.count(HookSubscriptionRevisionRecord.id))) == 1
