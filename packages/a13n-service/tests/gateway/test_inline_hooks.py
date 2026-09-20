from __future__ import annotations

from datetime import timedelta

import pytest
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.etags import resource_etag
from a13n_service.hooks import InlineHookSubscriptionInput, WebhookDestinationConfig
from a13n_service.hooks.models import HookSubscriptionRecord, HookSubscriptionRevisionRecord
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.interactions.command_values import RetryRunCommand, WaitingContinueRunCommand
from a13n_service.interactions.control_domain import InterruptRequest, WaitingRunFeedbackRequest
from a13n_service.interactions.errors import InteractionCommandError
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import short_session, transaction
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy import func, select

from tests.gateway.test_commands import _actor, _commands, _Freezing, _frozen, _Preparation, _request, _wait_run
from tests.hooks.support import SECRET_ID, seed_hook_actor_access
from tests.interactions.conftest import NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID

pytestmark = pytest.mark.anyio


def _hook(endpoint: str = "https://93.184.216.34/original") -> InlineHookSubscriptionInput:
    return InlineHookSubscriptionInput(
        hook_names=("run.accepted", "run.waiting", "run.cancelled", "run_attempt.succeeded"),
        webhook=WebhookDestinationConfig(endpoint_url=endpoint, signing_secret_id=SECRET_ID),
    )


async def _prepare(sessions, tmp_path, operation: str):
    await seed_hook_actor_access(sessions)
    async with transaction(sessions) as database:
        database.add(
            SecretRecord(
                id=SECRET_ID,
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                owner_type="workspace",
                owner_id=WORKSPACE_ID,
                key="hook-signing",
                version=1,
                ciphertext=b"ciphertext",
                nonce=b"0" * 12,
                encryption_key_id="test-key",
                created_at=NOW,
                value_updated_at=NOW,
                deleted_at=None,
            )
        )
    objects = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(sessions, objects, _Preparation(), _Freezing([_frozen()]))
    source = await commands.runs.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="source",
        request=_request().model_copy(update={"hook_subscription": _hook()}),
    )
    if operation == "retry":
        await commands.active.interrupt(
            actor=_actor(),
            run_id=source.run_id,
            idempotency_key="cancel-source",
            request=InterruptRequest(expected_run_version=1, expected_thread_version=1),
        )
        values = {"expected_thread_version": 2}
        request_type = RetryRunCommand
        command = commands.continuations.retry
    else:
        digest = await _wait_run(sessions, objects, run_id=source.run_id)
        values = {"expected_thread_version": 2, "sealed_state_digest_sha256": digest}
        if operation == "feedback":
            request_type = WaitingRunFeedbackRequest
            command = commands.continuations.feedback
        else:
            values["input"] = _request().input
            request_type = WaitingContinueRunCommand
            command = commands.continuations.continue_waiting
    return command, source, request_type, values


@pytest.mark.parametrize("operation", ["feedback", "continue_waiting", "retry"])
@pytest.mark.parametrize("selection", ["omitted", "null", "replacement"])
async def test_successor_hook_selection_expiry_and_replay(
    lifecycle_interaction_sessions, tmp_path, operation, selection
):
    sessions = lifecycle_interaction_sessions
    command, source, request_type, values = await _prepare(sessions, tmp_path, operation)
    replacement = _hook("https://93.184.216.34/replacement")
    if selection != "omitted":
        values["hook_subscription"] = None if selection == "null" else replacement
    request = request_type.model_validate(values)
    receipt = await command(actor=_actor(), run_id=source.run_id, idempotency_key="successor", request=request)
    async with short_session(sessions) as database:
        old = await database.get(HookSubscriptionRecord, source.hook_subscription_id)
        source_run = await database.get(RunRecord, source.run_id)
        assert old is not None and source_run is not None
        assert old.expired_at == source_run.sealed_at
        assert resource_etag(old.id, old.updated_at) != resource_etag(old.id, old.created_at)
        assert old.enabled and old.deleted_at is None
        assert (
            old.to_resource(await database.get(HookSubscriptionRevisionRecord, old.current_revision_id)).inline_run_id
            == source.run_id
        )
        delivered = set(
            await database.scalars(
                select(LifecycleEventRecord.event_type)
                .join(OutboxRecord, OutboxRecord.source_id == LifecycleEventRecord.id)
                .where(OutboxRecord.destination_ref == old.current_revision_id)
            )
        )
        assert "run.accepted" in delivered
        assert ("run.cancelled" if operation == "retry" else "run.waiting") in delivered
        if operation != "retry":
            assert "run_attempt.succeeded" in delivered
        if selection == "null":
            assert receipt.hook_subscription_id is None
        else:
            new = await database.get(HookSubscriptionRecord, receipt.hook_subscription_id)
            assert new is not None and new.id != old.id
            assert new.inline_run_id == receipt.run_id and new.expired_at is None
            assert new.version == 1 and new.current_revision_id != old.current_revision_id
            revision = await database.get(HookSubscriptionRevisionRecord, new.current_revision_id)
            assert revision is not None and revision.version == 1
            assert (revision.session_id, revision.thread_id, revision.run_id) == (
                source.session_id,
                source.thread_id,
                receipt.run_id,
            )
            assert revision.endpoint_url == (
                replacement.webhook.endpoint_url if selection == "replacement" else _hook().webhook.endpoint_url
            )
    # A successful replay returns its original receipt even if inheritance is now disabled.
    async with transaction(sessions) as database:
        old = await database.get(HookSubscriptionRecord, source.hook_subscription_id)
        old.enabled = False
        old.deleted_at = NOW + timedelta(seconds=10)
    assert await command(actor=_actor(), run_id=source.run_id, idempotency_key="successor", request=request) == receipt
    changed = request.model_copy(update={"hook_subscription": _hook() if selection == "null" else None})
    changed = await command(actor=_actor(), run_id=source.run_id, idempotency_key="successor", request=changed)
    assert changed == receipt


@pytest.mark.parametrize("operation", ["feedback", "continue_waiting", "retry"])
@pytest.mark.parametrize("manual_state", ["paused", "deleted"])
async def test_manual_state_blocks_default_inheritance(
    lifecycle_interaction_sessions, tmp_path, operation, manual_state
):
    sessions = lifecycle_interaction_sessions
    command, source, request_type, values = await _prepare(sessions, tmp_path, operation)
    async with transaction(sessions) as database:
        head = await database.get(HookSubscriptionRecord, source.hook_subscription_id)
        if manual_state == "paused":
            head.enabled = False
        else:
            head.deleted_at = NOW
    receipt = await command(
        actor=_actor(), run_id=source.run_id, idempotency_key="successor", request=request_type.model_validate(values)
    )
    assert receipt.hook_subscription_id is None


@pytest.mark.parametrize("unavailable", ["secret", "permission"])
async def test_inheritance_revalidates_authority_and_secret_atomically(
    lifecycle_interaction_sessions, tmp_path, unavailable
):
    sessions = lifecycle_interaction_sessions
    command, source, request_type, values = await _prepare(sessions, tmp_path, "retry")
    async with transaction(sessions) as database:
        if unavailable == "secret":
            secret = await database.get(SecretRecord, SECRET_ID)
            secret.deleted_at = NOW
            secret.ciphertext = secret.nonce = secret.encryption_key_id = None
        else:
            binding = await database.scalar(
                select(RoleBindingRecord).where(
                    RoleBindingRecord.principal_id == USER_ID,
                    RoleBindingRecord.resource_type == "workspace",
                )
            )
            binding.role_key = "runner"
    with pytest.raises(InteractionCommandError) as denied:
        await command(
            actor=_actor(),
            run_id=source.run_id,
            idempotency_key="successor",
            request=request_type.model_validate(values),
        )
    assert denied.value.code in {"inline_hook_secret_unavailable", "inline_hook_unauthorized"}
    async with short_session(sessions) as database:
        thread = await database.get(ThreadRecord, source.thread_id)
        assert (thread.version, thread.current_run_id) == (2, source.run_id)
        assert await database.scalar(select(func.count(RunRecord.id))) == 1
        assert await database.scalar(select(func.count(HookSubscriptionRecord.id))) == 1


async def test_retry_only_inherits_direct_source(lifecycle_interaction_sessions, tmp_path):
    sessions = lifecycle_interaction_sessions
    retry, source, _, _ = await _prepare(sessions, tmp_path, "retry")
    first = await retry(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="first",
        request=RetryRunCommand(expected_thread_version=2, hook_subscription=None),
    )
    objects = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(sessions, objects, _Preparation(), _Freezing([_frozen()]))
    await commands.active.interrupt(
        actor=_actor(),
        run_id=first.run_id,
        idempotency_key="cancel-first",
        request=InterruptRequest(expected_run_version=1, expected_thread_version=3),
    )
    second = await retry(
        actor=_actor(),
        run_id=first.run_id,
        idempotency_key="second",
        request=RetryRunCommand(expected_thread_version=4),
    )
    assert second.hook_subscription_id is None


@pytest.mark.parametrize("selection", ["omitted", "null", "replacement"])
async def test_native_waiting_submission_preserves_hook_selection(lifecycle_interaction_sessions, tmp_path, selection):
    from a13n_service.interactions.control_domain import ThreadRunSubmissionRequest, WaitingResolutionDefaults
    from a13n_service.interactions.queue import QueuedSubmissionStore
    from a13n_service.interactions.submissions import QueuedSubmissionService

    from tests.interactions.test_acceptance import _inline_hooks

    sessions = lifecycle_interaction_sessions
    _, source, _, values = await _prepare(sessions, tmp_path, "continue_waiting")
    objects = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(sessions, objects, _Preparation(), _Freezing([_frozen()]))
    service = QueuedSubmissionService(
        sessions, QueuedSubmissionStore(sessions, _inline_hooks(), clock=lambda: NOW), commands, clock=lambda: NOW
    )
    hook_values = (
        {}
        if selection == "omitted"
        else {"hook_subscription": None if selection == "null" else _hook("https://93.184.216.34/replaced")}
    )
    request = ThreadRunSubmissionRequest(
        expected_thread_version=2,
        input=_request().input,
        waiting_resolution=WaitingResolutionDefaults(sealed_state_digest_sha256=values["sealed_state_digest_sha256"]),
        **hook_values,
    )
    receipt = await service.submit(
        actor=_actor(), thread_id=source.thread_id, request=request, idempotency_key="native-waiting"
    )
    assert receipt.run is not None
    if selection == "null":
        assert receipt.run.hook_subscription_id is None
    else:
        async with short_session(sessions) as database:
            head = await database.get(HookSubscriptionRecord, receipt.run.hook_subscription_id)
            revision = await database.get(HookSubscriptionRevisionRecord, head.current_revision_id)
            assert revision.endpoint_url == (
                _hook().webhook.endpoint_url
                if selection == "omitted"
                else hook_values["hook_subscription"].webhook.endpoint_url
            )
    assert (
        await service.submit(
            actor=_actor(), thread_id=source.thread_id, request=request, idempotency_key="native-waiting"
        )
        == receipt
    )


async def test_pausing_source_during_preflight_rejects_acceptance(
    lifecycle_interaction_sessions, tmp_path, monkeypatch
):
    from a13n_service.hooks import InlineHookValidator

    sessions = lifecycle_interaction_sessions
    retry, source, _, _ = await _prepare(sessions, tmp_path, "retry")
    validate = InlineHookValidator.validate_destination

    async def pause_after_validation(self, subscription):
        await validate(self, subscription)
        async with transaction(sessions) as database:
            head = await database.get(HookSubscriptionRecord, source.hook_subscription_id)
            head.enabled = False

    monkeypatch.setattr(InlineHookValidator, "validate_destination", pause_after_validation)
    with pytest.raises(InteractionCommandError) as changed:
        await retry(
            actor=_actor(),
            run_id=source.run_id,
            idempotency_key="racing-retry",
            request=RetryRunCommand(expected_thread_version=2),
        )
    assert changed.value.code == "inline_hook_source_changed"
    async with short_session(sessions) as database:
        thread = await database.get(ThreadRecord, source.thread_id)
        assert (thread.version, thread.current_run_id) == (2, source.run_id)
        assert await database.scalar(select(func.count(RunRecord.id))) == 1


@pytest.mark.parametrize("explicit", [False, True])
async def test_only_explicit_selection_requires_command_actor_secret_authority(
    lifecycle_interaction_sessions, tmp_path, explicit
):
    from dataclasses import replace

    from a13n_service.iam import PrincipalRef
    from a13n_service.iam.models import UserRecord

    sessions = lifecycle_interaction_sessions
    retry, source, _, _ = await _prepare(sessions, tmp_path, "retry")
    runner_id = "usr_8282828282828282"
    async with transaction(sessions) as database:
        database.add(
            UserRecord(
                id=runner_id,
                email="runner@example.com",
                normalized_email="runner@example.com",
                name="Runner",
                status="active",
                email_verified_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await database.flush()
        for resource_type, resource_id, role, workspace_id in (
            ("organization", ORGANIZATION_ID, "member", None),
            ("workspace", WORKSPACE_ID, "runner", WORKSPACE_ID),
        ):
            database.add(
                RoleBindingRecord(
                    id=f"rb_runner_{resource_type}",
                    organization_id=ORGANIZATION_ID,
                    workspace_id=workspace_id,
                    principal_type="user",
                    principal_id=runner_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    role_key=role,
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
    runner = replace(_actor(), principal=PrincipalRef(principal_type="user", principal_id=runner_id))
    request = RetryRunCommand.model_validate(
        {"expected_thread_version": 2, **({"hook_subscription": _hook()} if explicit else {})}
    )
    if explicit:
        with pytest.raises(InteractionCommandError) as denied:
            await retry(actor=runner, run_id=source.run_id, idempotency_key="runner-retry", request=request)
        assert denied.value.code == "inline_hook_unauthorized"
    else:
        receipt = await retry(actor=runner, run_id=source.run_id, idempotency_key="runner-retry", request=request)
        assert receipt.hook_subscription_id is not None
