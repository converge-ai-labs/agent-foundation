from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_service.hooks.domain import InlineHookSubscriptionInput, WebhookDestinationConfig
from a13n_service.iam.models import UserRecord
from a13n_service.interactions.attempts import AttemptExecutionService
from a13n_service.interactions.control_models import QueuedSubmissionRecord
from a13n_service.interactions.harness_results import AttemptDisposition
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunPayloadStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.queue import QueuedSubmissionStore
from a13n_service.interactions.queue_completion import QueueCompletion
from a13n_service.interactions.queue_handoff import CompletionQueueHandoffService
from a13n_service.interactions.queue_recovery import QueueRecovery
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.interactions.terminal_committer import DatabaseAttemptCommitter
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.secrets.models import SecretRecord
from a13n_service.settings import Settings
from a13n_service.storage import short_session, transaction
from anyio import create_task_group, fail_after, sleep, sleep_forever
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import func, select

from tests.gateway.test_commands import _commands, _Freezing, _frozen, _Preparation
from tests.hooks.support import seed_hook_actor_access
from tests.lifecycle_support import test_lifecycle_writer

from .conftest import NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID
from .test_attempt_execution import _accept_root, _authority, _completed_state, _worker
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
    handoffs = CompletionQueueHandoffService(
        sessions, states, RunPayloadStore(objects), _inline_hooks(), clock=clock, lifecycle=test_lifecycle_writer()
    )
    completion = QueueCompletion(sessions, commands.queued, handoffs, clock=clock)
    committer = DatabaseAttemptCommitter(
        sessions,
        RunOutcomeService(sessions, RunPayloadStore(objects), clock=clock, lifecycle=test_lifecycle_writer()),
        execution,
        queue_completion=completion,
    )
    return source, authority, stored, queued.queued_submission, commands, completion, committer


@pytest.mark.parametrize("lost_commit_response", [False, True])
async def test_terminal_committer_completes_and_accepts_successor(
    interaction_sessions, interaction_object_store, monkeypatch, lost_commit_response
):
    sessions = interaction_sessions
    source, authority, stored, queued, commands, completion, committer = await _completion(
        sessions, interaction_object_store
    )
    if lost_commit_response:
        prepare = completion.prepare

        async def prepare_with_lost_response(*args):
            commit = await prepare(*args)
            assert commit is not None

            async def lose_response():
                await commit()
                raise ConnectionError("COMMIT response lost")

            return lose_response

        monkeypatch.setattr(completion, "prepare", prepare_with_lost_response)

    verified = await committer.verify_state_outcome(authority, stored)
    # Object preparation alone publishes no relational completion or successor.
    async with short_session(sessions) as database:
        assert (await database.get(RunRecord, source.id)).status == "running"
        assert await database.scalar(select(func.count()).select_from(RunRecord)) == 1

    outcome = await committer.commit_verified_state_outcome(authority, verified)
    assert outcome.disposition is AttemptDisposition.completed
    assert outcome.thread_version == 3
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
    assert (await QueueRecovery(sessions, commands.queued).scan()).completed == 0


@pytest.mark.parametrize("failure", ["dependency", "timeout", "commit_timeout"])
async def test_preparation_failure_completes_source_and_recovery_consumes_queue(
    interaction_sessions, interaction_object_store, monkeypatch, failure
):
    sessions = interaction_sessions
    source, authority, stored, queued, commands, completion, committer = await _completion(
        sessions, interaction_object_store
    )
    prepare = commands.queued.prepare_queued_run
    if failure == "timeout":
        authority = replace(authority, reconciliation_timeout=timedelta(milliseconds=50))
        monkeypatch.setattr(commands.queued, "prepare_queued_run", AsyncMock(side_effect=sleep_forever))
    elif failure == "commit_timeout":
        authority = replace(authority, reconciliation_timeout=timedelta(milliseconds=50))
        monkeypatch.setattr(completion, "prepare", AsyncMock(return_value=sleep_forever))
    else:
        monkeypatch.setattr(
            commands.queued, "prepare_queued_run", AsyncMock(side_effect=ConnectionError("dependency down"))
        )
    outcome = await committer.commit_verified_state_outcome(
        authority, await committer.verify_state_outcome(authority, stored)
    )
    assert outcome.disposition is AttemptDisposition.completed
    assert outcome.thread_version == 2
    async with short_session(sessions) as database:
        assert (await database.get(RunRecord, source.id)).status == "completed"
        entry = await database.get(QueuedSubmissionRecord, queued.queued_submission_id)
        assert entry.position == 1 and entry.to_resource().state == "queued" and entry.failure_json is None
    monkeypatch.setattr(commands.queued, "prepare_queued_run", prepare)
    assert (await QueueRecovery(sessions, commands.queued).scan()).completed == 1


@pytest.mark.parametrize("change", ["principal_disabled", "queue_version"])
async def test_final_revalidation_rolls_back_handoff_and_preserves_recoverable_queue(
    interaction_sessions, interaction_object_store, change
):
    sessions = interaction_sessions
    source, authority, stored, queued, commands, _completion_service, committer = await _completion(
        sessions, interaction_object_store
    )
    verified = await committer.verify_state_outcome(authority, stored)
    async with transaction(sessions) as database:
        if change == "principal_disabled":
            (await database.get(UserRecord, USER_ID)).status = "disabled"
        else:
            (await database.get(ThreadRecord, source.thread_id)).queue_version += 1
    outcome = await committer.commit_verified_state_outcome(authority, verified)
    assert outcome.disposition is AttemptDisposition.completed
    assert outcome.thread_version == 2
    async with short_session(sessions) as database:
        entry = await database.get(QueuedSubmissionRecord, queued.queued_submission_id)
        assert entry.position == 1 and entry.consumed_run_id is None and entry.failure_json is None
        assert await database.scalar(select(func.count()).select_from(RunRecord)) == 1
    async with transaction(sessions) as database:
        (await database.get(UserRecord, USER_ID)).status = "active"
    assert (await QueueRecovery(sessions, commands.queued).scan()).completed == 1


@pytest.mark.parametrize("deletion_proof_changes", [False, True])
async def test_permanent_failure_requires_deletion_proof_at_combined_commit(
    interaction_sessions, interaction_object_store, deletion_proof_changes
):
    sessions = interaction_sessions
    source, authority, stored, queued, _commands_service, _completion_service, committer = await _completion(
        sessions, interaction_object_store, queued_hook=True
    )
    async with transaction(sessions) as database:
        secret = await database.get(SecretRecord, SECRET_ID)
        secret.deleted_at = NOW
        secret.ciphertext = secret.nonce = secret.encryption_key_id = None
    verified = await committer.verify_state_outcome(authority, stored)
    if deletion_proof_changes:
        async with transaction(sessions) as database:
            secret = await database.get(SecretRecord, SECRET_ID)
            secret.deleted_at = None
            secret.ciphertext, secret.nonce, secret.encryption_key_id = b"ciphertext", b"3" * 12, "test-key"
    outcome = await committer.commit_verified_state_outcome(authority, verified)
    assert outcome.disposition is AttemptDisposition.completed
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
                            assert entry.consumed_run_id is not None
                            successor = await database.get(RunRecord, entry.consumed_run_id)
                            assert successor.parent_run_id == source.id
                            assert successor.authority_principal_id == USER_ID
                            break
                    await sleep(0.01)
                await loop.drain()
                await loop.wait_stopped()
