import anyio
import pytest
from a13n_service.iam.models import UserRecord, WorkspaceRecord
from a13n_service.interactions.control_models import QueuedSubmissionRecord
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.queue import QueuedSubmissionStore
from a13n_service.interactions.recovery import QueueRecovery
from a13n_service.storage import short_session, transaction
from sqlalchemy import select

from tests.gateway.test_commands import _actor, _commands, _Freezing, _frozen, _Preparation, _request
from tests.hooks.support import seed_hook_actor_access
from tests.interactions.conftest import NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID
from tests.interactions.test_queue import _fail_current_run, _inline_hooks, _intent, _principal

pytestmark = pytest.mark.anyio


async def _queued(sessions, objects):
    await seed_hook_actor_access(sessions)
    commands = _commands(sessions, objects, _Preparation(), _Freezing([_frozen()]))
    receipt = await commands.start(
        actor=_actor(), workspace_id=WORKSPACE_ID, request=_request(), idempotency_key="queue-recovery-root"
    )
    queue = QueuedSubmissionStore(sessions, _inline_hooks(), clock=lambda: NOW)
    queued = await queue.enqueue(
        organization_id=ORGANIZATION_ID,
        thread_id=receipt.thread_id,
        expected_thread_version=1,
        authority_principal=_principal(),
        submission=_intent("recover without another request"),
        queued_submission_id="qsub_9494949494949494",
    )
    await _fail_current_run(sessions, run_id=receipt.run_id, thread_id=receipt.thread_id)
    return commands, receipt, queued.queued_submission


async def test_overlapping_scans_accept_exactly_one_queued_run(interaction_sessions, interaction_object_store):
    sessions = interaction_sessions
    commands, source, queued = await _queued(sessions, interaction_object_store)
    results = []

    async def scan():
        results.append(await QueueRecovery(sessions, commands).scan())

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(scan)
        tasks.start_soon(scan)
    assert sum(result.completed for result in results) == 1
    async with short_session(sessions) as database:
        row = await database.get(QueuedSubmissionRecord, queued.queued_submission_id)
        assert row.consumed_run_id is not None and row.position is None
        run = await database.get(RunRecord, row.consumed_run_id)
        thread = await database.get(ThreadRecord, source.thread_id)
        assert thread.current_run_id == run.id
        assert run.authority_principal_id == USER_ID
        assert run.trigger_type == "queued_submission"
        assert len(tuple(await database.scalars(select(RunRecord.id)))) == 2


async def test_reversible_disablement_defers_but_owner_deletion_fails_intent(
    interaction_sessions, interaction_object_store
):
    sessions = interaction_sessions
    commands, source, queued = await _queued(sessions, interaction_object_store)
    async with transaction(sessions) as database:
        user = await database.get(UserRecord, USER_ID)
        user.status = "disabled"
    collector = QueueRecovery(sessions, commands)
    assert (await collector.scan()).deferred == 1
    async with transaction(sessions) as database:
        row = await database.get(QueuedSubmissionRecord, queued.queued_submission_id)
        assert row.position == 1 and row.failure_json is None
        workspace = await database.get(WorkspaceRecord, WORKSPACE_ID)
        workspace.deleted_at = NOW
    # A fresh replica recovers entirely from owning durable records.
    assert (await QueueRecovery(sessions, commands).scan()).completed == 1
    async with short_session(sessions) as database:
        row = await database.get(QueuedSubmissionRecord, queued.queued_submission_id)
        thread = await database.get(ThreadRecord, source.thread_id)
        assert row.failure_json["code"] == "workspace_deleted"
        assert row.position is None and row.consumed_run_id is None
        assert thread.current_run_id == source.run_id
        assert thread.version == 2
        assert thread.queue_version == 2
