"""Measure command SQL at real storage boundaries, excluding fixture setup."""

from functools import partial

import pytest
from a13n_service.interactions.command_values import ContinueRunCommand, ForkRunCommand
from a13n_service.interactions.control_domain import (
    ConsumeQueuedSubmissionRequest,
    InterruptRequest,
    ThreadRunSubmissionRequest,
)
from a13n_service.interactions.models import RunRecord
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.storage import short_session
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy import select

from tests.gateway.test_commands import _actor, _commands, _complete_run, _Freezing, _frozen, _Preparation, _request
from tests.gateway.test_inline_hooks import _prepare
from tests.gateway.test_queue import _submission_setup
from tests.hooks.support import seed_hook_actor_access
from tests.interactions.conftest import WORKSPACE_ID
from tests.sql_capture import capture_sql

pytestmark = pytest.mark.anyio


def _assert_acceptance_sql(operation, statements, *, new_run=True):
    normalized = [" ".join(sql.split()) for sql in statements]
    # These are cursor executions, not expanded executemany groups or HTTP
    # round trips. Authentication and fixture mutations are outside the sample.
    print(f"acceptance_sql operation={operation} cursor_executions={len(statements)}")
    assert not any(sql.startswith("SELECT max(lifecycle_events.resource_seq)") for sql in normalized)
    assert not any(sql.startswith("SELECT sessions.") and " JOIN " not in sql for sql in normalized)
    if new_run:
        assert not any(sql.startswith("SELECT run_environment_mounts.environment_id ") for sql in normalized)
    assert not any("FOR SHARE OF workspaces" in sql for sql in normalized)
    assert not any("hook_names @>" in sql and "FOR UPDATE OF hook_subscriptions" in sql for sql in normalized)
    assert sum("INSERT INTO lifecycle_events " in sql for sql in statements) == 1


async def _assert_committed(sessions, receipt):
    async with short_session(sessions) as database:
        run = await database.get(RunRecord, receipt.run_id)
        event = await database.scalar(select(LifecycleEventRecord).where(LifecycleEventRecord.run_id == run.id))
        assert run.status == "accepted" and run.lifecycle_seq == 1
        assert event.event_type == "run.accepted" and event.resource_seq == 1 and event.entity_version == run.version


@pytest.mark.parametrize("operation", ["start", "continue", "fork"])
async def test_run_commands_reuse_scope_and_allocate_events_without_readback(
    lifecycle_interaction_sessions, tmp_path, operation
):
    sessions = lifecycle_interaction_sessions
    await seed_hook_actor_access(sessions)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(sessions, objects, _Preparation(), _Freezing([_frozen()]))
    if operation == "start":
        invoke = partial(
            commands.runs.start,
            actor=_actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="measured",
            request=_request(),
        )
    else:
        source = await commands.runs.start(
            actor=_actor(), workspace_id=WORKSPACE_ID, idempotency_key="source", request=_request()
        )
        await _complete_run(sessions, objects, run_id=source.run_id)
        if operation == "continue":
            invoke = partial(
                commands.runs.continue_from,
                actor=_actor(),
                source_run_id=source.run_id,
                idempotency_key="measured",
                request=ContinueRunCommand(expected_thread_version=2, input=_request().input),
            )
        else:
            invoke = partial(
                commands.runs.fork,
                actor=_actor(),
                run_id=source.run_id,
                idempotency_key="measured",
                request=ForkRunCommand(input=_request().input),
            )
    with capture_sql(sessions) as statements:
        receipt = await invoke()
    _assert_acceptance_sql(operation, statements)
    await _assert_committed(sessions, receipt)
    assert await invoke() == receipt


async def test_interrupt_reuses_session_scope(lifecycle_interaction_sessions, tmp_path):
    sessions = lifecycle_interaction_sessions
    await seed_hook_actor_access(sessions)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(sessions, objects, _Preparation(), _Freezing([_frozen()]))
    source = await commands.runs.start(
        actor=_actor(), workspace_id=WORKSPACE_ID, idempotency_key="source", request=_request()
    )
    invoke = partial(
        commands.active.interrupt,
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="interrupt",
        request=InterruptRequest(expected_run_version=1, expected_thread_version=1),
    )
    with capture_sql(sessions) as statements:
        receipt = await invoke()
    _assert_acceptance_sql("interrupt", statements, new_run=False)
    async with short_session(sessions) as database:
        run = await database.get(RunRecord, source.run_id)
        assert run.status == "cancelled" and run.version == 2 and run.lifecycle_seq == 2
    assert await invoke() == receipt


@pytest.mark.parametrize("operation", ["feedback", "waiting_continue", "retry"])
async def test_retained_commands_reuse_scope_with_inline_hooks(lifecycle_interaction_sessions, tmp_path, operation):
    sessions = lifecycle_interaction_sessions
    command, source, request_type, values = await _prepare(sessions, tmp_path, operation)
    invoke = partial(
        command, actor=_actor(), run_id=source.run_id, idempotency_key="measured", request=request_type(**values)
    )
    with capture_sql(sessions) as statements:
        receipt = await invoke()
    _assert_acceptance_sql(operation, statements)
    assert receipt.hook_subscription_id is not None
    await _assert_committed(sessions, receipt)
    assert await invoke() == receipt


@pytest.mark.parametrize("outcome", ["cancelled", "completed", "cancelled_successor"])
async def test_queue_consumption_reuses_observed_session(lifecycle_interaction_sessions, tmp_path, outcome):
    sessions = lifecycle_interaction_sessions
    await seed_hook_actor_access(sessions)
    service, commands, objects, source = await _submission_setup(sessions, tmp_path)
    version = 1
    if outcome == "cancelled_successor":
        await _complete_run(sessions, objects, run_id=source.run_id)
        source = await commands.runs.continue_from(
            actor=_actor(),
            source_run_id=source.run_id,
            idempotency_key="successor",
            request=ContinueRunCommand(expected_thread_version=2, input=_request().input),
        )
        version = 3
    queued = await service.submit(
        actor=_actor(),
        thread_id=source.thread_id,
        idempotency_key="queued",
        request=ThreadRunSubmissionRequest(expected_thread_version=version, input=_request().input),
    )
    if outcome == "completed":
        await _complete_run(sessions, objects, run_id=source.run_id)
    else:
        await commands.active.interrupt(
            actor=_actor(),
            run_id=source.run_id,
            idempotency_key="interrupt",
            request=InterruptRequest(expected_run_version=1, expected_thread_version=version),
        )
    with capture_sql(sessions) as statements:
        receipt = await service.consume(
            actor=_actor(),
            thread_id=source.thread_id,
            idempotency_key="measured",
            request=ConsumeQueuedSubmissionRequest(expected_thread_version=version + 1, expected_queue_version=1),
        )
    _assert_acceptance_sql(f"queue_consume_{outcome}", statements)
    assert (
        receipt.run is not None
        and receipt.queued_submission.queued_submission_id == queued.queued_submission.queued_submission_id
    )
    await _assert_committed(sessions, receipt.run)
