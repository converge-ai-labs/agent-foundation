from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_service.hooks.domain import InlineHookSubscriptionInput, WebhookDestinationConfig
from a13n_service.hooks.models import HookSubscriptionRecord
from a13n_service.hooks.persistence import create_inline_hook_subscription
from a13n_service.iam.models import UserRecord
from a13n_service.interactions.attempts import AttemptExecutionService
from a13n_service.interactions.control_models import QueuedSubmissionRecord
from a13n_service.interactions.harness_results import AttemptDisposition
from a13n_service.interactions.inbox import ThreadInboxStore
from a13n_service.interactions.inline_hooks import InlineHookAcceptance
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunPayloadStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.queue import QueuedSubmissionStore
from a13n_service.interactions.queue_drain import QueueDrain
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.interactions.terminal_committer import DatabaseAttemptCommitter
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.secrets.models import SecretRecord
from a13n_service.settings import Settings
from a13n_service.storage import short_session, transaction
from anyio import Event, create_task_group, fail_after, sleep, sleep_forever
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import func, select

from tests.gateway.test_commands import _commands, _Freezing, _frozen, _Preparation
from tests.hooks.support import seed_hook_actor_access
from tests.lifecycle_support import test_lifecycle_writer

from .conftest import NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID
from .test_attempt_execution import _accept_root, _authority, _completed_state, _worker
from .test_inbox import _input
from .test_queue import _inline_hooks, _intent, _principal, _RecordingEndpoint
from .worker_helpers import worker_runtime

pytestmark = pytest.mark.anyio


SECRET_ID = "sec_9595959595959595"


async def _completion(sessions, objects, *, queued_hook=False):
    states, source, initial = await _accept_root(sessions, objects)
    await seed_hook_actor_access(sessions)

    def clock():
        return NOW + timedelta(seconds=4)

    claim = await AttemptScheduler(sessions, clock=clock, lifecycle=test_lifecycle_writer()).claim(source.id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    authority = _authority(claim)
    execution = AttemptExecutionService(sessions, clock=clock, lifecycle=test_lifecycle_writer())
    preparation = await execution.commit_preparation_success(authority)
    await execution.enter_harness(authority, preparation=preparation, harness_run_id="completion-test")
    stored = await execution.publish_checkpoint(
        authority,
        states,
        await states.read(ORGANIZATION_ID, source.id),
        _completed_state(initial, claim.attempt.id, claim.attempt.attempt_number),
    )
    submission = _intent("run after completion")
    if queued_hook:
        async with transaction(sessions) as database:
            database.add(
                SecretRecord(
                    id=SECRET_ID,
                    organization_id=ORGANIZATION_ID,
                    workspace_id=WORKSPACE_ID,
                    owner_type="workspace",
                    owner_id=WORKSPACE_ID,
                    key="queued-hook",
                    version=1,
                    ciphertext=b"ciphertext",
                    nonce=b"3" * 12,
                    encryption_key_id="test-key",
                    created_at=NOW,
                    value_updated_at=NOW,
                    deleted_at=None,
                )
            )
        submission = submission.model_copy(
            update={
                "hook_subscription": InlineHookSubscriptionInput(
                    hook_names=("run.accepted",),
                    webhook=WebhookDestinationConfig(
                        endpoint_url="https://hooks.example.com/queued", signing_secret_id=SECRET_ID
                    ),
                )
            }
        )
    queue = QueuedSubmissionStore(sessions, _inline_hooks(_RecordingEndpoint()), clock=clock)
    queued = await queue.enqueue(
        organization_id=ORGANIZATION_ID,
        thread_id=source.thread_id,
        expected_thread_version=1,
        authority_principal=_principal(),
        submission=submission,
        queued_submission_id="qsub_9595959595959595",
    )
    commands = _commands(sessions, objects, _Preparation(), _Freezing([_frozen()]))
    drain = QueueDrain(sessions, commands.queued, clock=clock)
    committer = DatabaseAttemptCommitter(
        sessions,
        RunOutcomeService(sessions, RunPayloadStore(objects), clock=clock, lifecycle=test_lifecycle_writer()),
        execution,
    )
    return source, authority, stored, queued.queued_submission, commands, drain, committer


@pytest.mark.parametrize("lost_commit_response", [False, True])
async def test_source_completion_precedes_successor_preparation(
    interaction_sessions, interaction_object_store, monkeypatch, lost_commit_response
):
    sessions = interaction_sessions
    source, authority, stored, queued, commands, drain, committer = await _completion(
        sessions, interaction_object_store
    )
    prepare = commands.queued.prepare_queued_run

    async def prepare_after_completion(**kwargs):
        async with short_session(sessions) as database:
            assert (await database.get(RunRecord, source.id)).status == "completed"
            assert (await database.get(RunAttemptRecord, authority.run_attempt_id)).status == "succeeded"
            assert await database.scalar(select(func.count()).select_from(RunRecord)) == 1
        return await prepare(**kwargs)

    monkeypatch.setattr(commands.queued, "prepare_queued_run", prepare_after_completion)
    outcome = await committer.commit_verified_state_outcome(
        authority, await committer.verify_state_outcome(authority, stored)
    )
    assert outcome.disposition is AttemptDisposition.completed
    assert outcome.thread_version == 2
    if lost_commit_response:
        consume = commands.queued.recover_queued

        async def lose_response(**kwargs):
            await consume(**kwargs)
            raise ConnectionError("COMMIT response lost")

        monkeypatch.setattr(commands.queued, "recover_queued", lose_response)
        with pytest.raises(ConnectionError):
            await drain.consume_thread(organization_id=ORGANIZATION_ID, thread_id=source.thread_id)
    else:
        assert await drain.consume_thread(organization_id=ORGANIZATION_ID, thread_id=source.thread_id)
    async with short_session(sessions) as database:
        run = await database.get(RunRecord, source.id)
        attempt = await database.get(RunAttemptRecord, authority.run_attempt_id)
        entry = await database.get(QueuedSubmissionRecord, queued.queued_submission_id)
        successor = await database.get(RunRecord, entry.consumed_run_id)
        thread = await database.get(ThreadRecord, source.thread_id)
        assert (run.status, attempt.status, entry.to_resource().state) == ("completed", "succeeded", "consumed")
        assert successor.parent_run_id == source.id
        assert successor.authority_principal_id == USER_ID
        assert successor.trigger_type == "queued_submission"
        assert (thread.version, thread.queue_version, thread.head_run_id, thread.current_run_id) == (
            3,
            2,
            source.id,
            successor.id,
        )
        assert await database.scalar(select(func.count()).select_from(RunRecord)) == 2
    assert (await QueueDrain(sessions, commands.queued).scan()).completed == 0


async def test_late_steer_blocks_completion_and_queue_consumption(interaction_sessions, interaction_object_store):
    sessions = interaction_sessions
    source, authority, stored, queued, _, drain, committer = await _completion(sessions, interaction_object_store)
    verified = await committer.verify_state_outcome(authority, stored)
    payload = _input("arrived after completion preparation")
    await ThreadInboxStore(sessions, clock=lambda: NOW + timedelta(seconds=5)).append_steer(
        organization_id=ORGANIZATION_ID, run_id=source.id, input=payload
    )
    outcome = await committer.commit_verified_state_outcome(authority, verified)
    assert outcome.disposition is AttemptDisposition.continuing
    assert not await drain.consume_thread(organization_id=ORGANIZATION_ID, thread_id=source.thread_id)
    async with short_session(sessions) as database:
        thread = await database.get(ThreadRecord, source.thread_id)
        run = await database.get(RunRecord, source.id)
        entry = await database.get(QueuedSubmissionRecord, queued.queued_submission_id)
        assert (thread.next_delivery_sequence, thread.pending_count, thread.pending_bytes) == (
            2,
            1,
            len(payload.canonical_bytes()),
        )
        assert (thread.version, thread.queue_version, thread.current_run_id, thread.head_run_id) == (
            1,
            1,
            source.id,
            None,
        )
        assert run.status == "running" and entry.position == 1 and entry.consumed_run_id is None
        assert await database.scalar(select(func.count()).select_from(RunRecord)) == 1


@pytest.mark.parametrize("failure", ["dependency", "timeout", "interruption"])
async def test_drain_failure_preserves_completed_source_and_scanner_recovers(
    interaction_sessions, interaction_object_store, monkeypatch, failure
):
    sessions = interaction_sessions
    source, authority, stored, queued, commands, _, committer = await _completion(sessions, interaction_object_store)
    await committer.commit_verified_state_outcome(authority, await committer.verify_state_outcome(authority, stored))
    prepare = commands.queued.prepare_queued_run
    drain = QueueDrain(sessions, commands.queued, item_timeout_seconds=0.05 if failure == "timeout" else 30)
    if failure == "timeout":

        async def stall(**_kwargs):
            await sleep_forever()

        monkeypatch.setattr(commands.queued, "prepare_queued_run", stall)
        assert not await drain.consume_thread(organization_id=ORGANIZATION_ID, thread_id=source.thread_id)
    elif failure == "dependency":
        monkeypatch.setattr(commands.queued, "prepare_queued_run", AsyncMock(side_effect=ConnectionError("down")))
        with pytest.raises(ConnectionError):
            await drain.consume_thread(organization_id=ORGANIZATION_ID, thread_id=source.thread_id)
    # Interruption immediately after A commits requires no queue callback to have run.
    async with short_session(sessions) as database:
        assert (await database.get(RunRecord, source.id)).status == "completed"
        entry = await database.get(QueuedSubmissionRecord, queued.queued_submission_id)
        assert entry.position == 1 and entry.to_resource().state == "queued" and entry.failure_json is None
    monkeypatch.setattr(commands.queued, "prepare_queued_run", prepare)
    assert (await QueueDrain(sessions, commands.queued).scan()).completed == 1


@pytest.mark.parametrize("change", ["principal_disabled", "queue_version"])
async def test_drain_retains_checked_authority_but_revalidates_queue_version(
    interaction_sessions, interaction_object_store, monkeypatch, change
):
    sessions = interaction_sessions
    source, authority, stored, queued, commands, drain, committer = await _completion(
        sessions, interaction_object_store
    )
    await committer.commit_verified_state_outcome(authority, await committer.verify_state_outcome(authority, stored))
    prepare = commands.queued.prepare_queued_run

    async def change_after_preparation(**kwargs):
        prepared = await prepare(**kwargs)
        async with transaction(sessions) as database:
            if change == "principal_disabled":
                (await database.get(UserRecord, USER_ID)).status = "disabled"
            else:
                (await database.get(ThreadRecord, source.thread_id)).queue_version += 1
        return prepared

    monkeypatch.setattr(commands.queued, "prepare_queued_run", change_after_preparation)
    consumed = await drain.consume_thread(organization_id=ORGANIZATION_ID, thread_id=source.thread_id)
    assert consumed is (change == "principal_disabled")
    async with short_session(sessions) as database:
        entry = await database.get(QueuedSubmissionRecord, queued.queued_submission_id)
        assert entry.failure_json is None
        assert (await database.get(RunRecord, source.id)).status == "completed"
        if change == "principal_disabled":
            assert entry.consumed_run_id is not None
            assert await database.scalar(select(func.count()).select_from(RunRecord)) == 2
            return
        assert entry.position == 1 and entry.consumed_run_id is None
        assert await database.scalar(select(func.count()).select_from(RunRecord)) == 1
    async with transaction(sessions) as database:
        (await database.get(UserRecord, USER_ID)).status = "active"
    monkeypatch.setattr(commands.queued, "prepare_queued_run", prepare)
    assert (await QueueDrain(sessions, commands.queued).scan()).completed == 1


@pytest.mark.parametrize("deletion_proof_changes", [False, True])
async def test_permanent_failure_revalidates_deletion_proof_after_source_completed(
    interaction_sessions, interaction_object_store, monkeypatch, deletion_proof_changes
):
    sessions = interaction_sessions
    source, authority, stored, queued, commands, drain, committer = await _completion(
        sessions, interaction_object_store, queued_hook=True
    )
    await committer.commit_verified_state_outcome(authority, await committer.verify_state_outcome(authority, stored))
    async with transaction(sessions) as database:
        secret = await database.get(SecretRecord, SECRET_ID)
        secret.deleted_at = NOW
        secret.ciphertext = secret.nonce = secret.encryption_key_id = None
    if deletion_proof_changes:
        fail = commands.queued._acceptance.fail_queued_permanently

        async def restore_before_commit(**kwargs):
            async with transaction(sessions) as database:
                secret = await database.get(SecretRecord, SECRET_ID)
                secret.deleted_at = None
                secret.ciphertext, secret.nonce, secret.encryption_key_id = b"ciphertext", b"3" * 12, "test-key"
            return await fail(**kwargs)

        monkeypatch.setattr(commands.queued._acceptance, "fail_queued_permanently", restore_before_commit)
    assert (
        await drain.consume_thread(organization_id=ORGANIZATION_ID, thread_id=source.thread_id)
        is not deletion_proof_changes
    )
    async with short_session(sessions) as database:
        assert (await database.get(RunRecord, source.id)).status == "completed"
        entry = await database.get(QueuedSubmissionRecord, queued.queued_submission_id)
        thread = await database.get(ThreadRecord, source.thread_id)
        assert entry.consumed_run_id is None
        assert (thread.version, thread.current_run_id) == (2, source.id)
        if deletion_proof_changes:
            assert entry.to_resource().state == "queued" and entry.failure_json is None and entry.position == 1
            assert thread.queue_version == 1
        else:
            assert entry.to_resource().state == "failed" and entry.failure_json["code"] == "secret_deleted"
            assert entry.position is None and thread.queue_version == 2


async def test_worker_composition_consumes_queue_when_source_finishes(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch
):
    sessions = interaction_sessions
    await seed_hook_actor_access(sessions)
    _, source, _ = await _accept_root(sessions, interaction_object_store)
    queued = await QueuedSubmissionStore(sessions, _inline_hooks()).enqueue(
        organization_id=ORGANIZATION_ID,
        thread_id=source.thread_id,
        expected_thread_version=1,
        authority_principal=_principal(),
        submission=_intent("next from the worker"),
        queued_submission_id="qsub_9696969696969696",
    )

    async def respond(_messages, _info):
        yield "completed by worker"

    model_factory = Mock(spec=NativeModelFactory)
    model_factory.build.return_value = FunctionModel(stream_function=respond)
    consume = QueueDrain.consume_thread
    completion_seen = Event()

    async def observe_completed(self, *, organization_id, thread_id):
        if thread_id == source.thread_id and not completion_seen.is_set():
            async with short_session(sessions) as database:
                assert (await database.get(RunRecord, source.id)).status == "completed"
                entry = await database.get(QueuedSubmissionRecord, queued.queued_submission.queued_submission_id)
                assert entry.consumed_run_id is None
            completion_seen.set()
        return await consume(self, organization_id=organization_id, thread_id=thread_id)

    monkeypatch.setattr(QueueDrain, "consume_thread", observe_completed)
    settings = Settings(worker={"concurrency": 1, "poll_interval_seconds": 0.01})
    invocations = SimpleNamespace(preparation=_Preparation(), freezing=_Freezing([_frozen()]))
    async with worker_runtime(
        sessions,
        interaction_object_store,
        tmp_path,
        monkeypatch,
        settings=settings,
        model_factory=model_factory,
        invocations=invocations,
    ) as (runtime, _shared):
        loop = runtime.execution_loop
        # No Control recovery scanner runs in this composition.
        with fail_after(15):
            async with create_task_group() as tasks:
                tasks.start_soon(loop.run)
                while True:
                    async with short_session(sessions) as database:
                        run = await database.get(RunRecord, source.id)
                        if run.status in {"completed", "failed"}:
                            assert run.status == "completed", run.failure_json
                            entry = await database.get(
                                QueuedSubmissionRecord, queued.queued_submission.queued_submission_id
                            )
                            if entry.consumed_run_id is None:
                                continue
                            successor = await database.get(RunRecord, entry.consumed_run_id)
                            assert successor.parent_run_id == source.id
                            assert successor.authority_principal_id == USER_ID
                            break
                    await sleep(0.01)
                assert completion_seen.is_set()
                await loop.drain()
                await loop.wait_stopped()


async def test_source_hook_capacity_is_released_before_queued_successor_acceptance(
    interaction_sessions, interaction_object_store, monkeypatch
):
    sessions = interaction_sessions
    source, authority, stored, queued, commands, drain, committer = await _completion(
        sessions, interaction_object_store, queued_hook=True
    )
    monkeypatch.setattr("a13n_service.hooks.persistence.MAX_ACTIVE_HOOK_SUBSCRIPTIONS", 1)
    async with transaction(sessions) as database:
        source_hook = await create_inline_hook_subscription(
            database,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            session_id=source.session_id,
            thread_id=source.thread_id,
            run_id=source.id,
            actor_type="user",
            actor_id=USER_ID,
            subscription=queued.submission.hook_subscription,
            now=NOW,
        )
    await committer.commit_verified_state_outcome(authority, await committer.verify_state_outcome(authority, stored))
    async with short_session(sessions) as database:
        expired = await database.get(HookSubscriptionRecord, source_hook.id)
        run = await database.get(RunRecord, source.id)
        assert expired.expired_at == run.sealed_at
    monkeypatch.setattr(
        commands.queued._acceptance,
        "_inline_hooks",
        InlineHookAcceptance(sessions, _inline_hooks(_RecordingEndpoint())),
    )
    assert await drain.consume_thread(organization_id=ORGANIZATION_ID, thread_id=source.thread_id)
    async with short_session(sessions) as database:
        entry = await database.get(QueuedSubmissionRecord, queued.queued_submission_id)
        hooks = list(
            await database.scalars(select(HookSubscriptionRecord).where(HookSubscriptionRecord.expired_at.is_(None)))
        )
        assert len(hooks) == 1 and hooks[0].inline_run_id == entry.consumed_run_id
