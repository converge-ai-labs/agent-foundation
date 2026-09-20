"""PostgreSQL cursor executions, including Run-authority IAM and excluding HTTP authentication.

Baseline b1149707: delegate 30, resume 40, publication 18, publication replay 15,
active materialization 15, successor 37, successor scan 43. Fixtures use one parent
and one child without optional resources; publication uses a sealed inline failure.
"""

from collections import Counter
from datetime import timedelta

import pytest
from a13n_harness.capabilities import AsyncDelegateRequest, AsyncResumeRequest
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.scheduling import AttemptScheduler
from a13n_service.run_stream import RunDisplayStore
from a13n_service.storage import short_session
from a13n_service.subagents import (
    AsyncSubagentResultMaterializer,
    AsyncSubagentResultPublisher,
    AsyncSubagentSuccessorReconciler,
)

from tests.lifecycle_support import test_lifecycle_writer
from tests.memory.selection_support import ordinary_memory
from tests.sql_capture import capture_sql

from .conftest import NOW, ORGANIZATION_ID
from .test_attempt_execution import _authority, _worker
from .test_subagent_acceptance import _complete_run
from .test_subagent_operator import _operator, _plan, _run
from .test_subagent_results import _accept_child, _fail_child
from .test_subagent_successors import _seal_parent

pytestmark = pytest.mark.anyio


def _assert_database_work(operation, statements, max_executions, *, state_reads=None):
    # before_cursor_execute events include commit-time flushes, but not BEGIN/COMMIT or fixture setup.
    print(f"subagent_sql operation={operation} cursor_executions={len(statements)} state_reads={state_reads}")
    assert len(statements) <= max_executions


async def test_delegate_and_resume_database_work(interaction_sessions, interaction_object_store, monkeypatch):
    sessions = interaction_sessions
    operator, context, plan, states, _, _ = await _operator(sessions, interaction_object_store)
    reads = Counter()
    read_run = states.read_run

    async def counted_read(run):
        reads[run.id] += 1
        return await read_run(run)

    monkeypatch.setattr(states, "read_run", counted_read)
    with capture_sql(sessions) as statements:
        delegated = await operator.delegate(plan, AsyncDelegateRequest(subagent_name="researcher", prompt="research"))
    _assert_database_work("delegate", statements, 27, state_reads=dict(reads))
    assert reads == {context.parent_run_id: 1}
    assert delegated.child_run_id is not None
    claim = await AttemptScheduler(
        sessions,
        clock=lambda: NOW + timedelta(seconds=3),
        token_factory=lambda: "child-lease",
        attempt_id_factory=lambda: "rat_6767676767676767",
        lifecycle=test_lifecycle_writer(),
    ).claim(delegated.child_run_id, _worker())
    assert claim is not None
    await _complete_run(
        sessions, interaction_object_store, states, await _run(sessions, delegated.child_run_id), _authority(claim)
    )
    reads.clear()
    with capture_sql(sessions) as statements:
        resumed = await operator.resume(
            _plan(context, delegated_input='{"delegated_task":"continue"}'),
            AsyncResumeRequest(execution_id=delegated.execution_id, prompt="continue"),
        )
    _assert_database_work("resume", statements, 36, state_reads=dict(reads))
    assert reads == {context.parent_run_id: 1, delegated.child_run_id: 1}
    assert resumed.resumed_from == delegated.execution_id
    assert resumed.thread_id == delegated.thread_id
    assert resumed.child_run_id != delegated.child_run_id
    assert resumed.segment_index == 1
    async with short_session(sessions) as database:
        run = await database.get(RunRecord, resumed.child_run_id)
        thread = await database.get(ThreadRecord, resumed.thread_id)
        assert run.parent_run_id == delegated.child_run_id
        assert thread.current_run_id == resumed.child_run_id


@pytest.mark.parametrize("delivery", ["active", "successor", "scan"])
async def test_result_publication_and_delivery_database_work(interaction_sessions, interaction_object_store, delivery):
    sessions = interaction_sessions
    states, parent, authority, child_run_id = await _accept_child(sessions, interaction_object_store)
    if delivery != "active":
        await _seal_parent(sessions, interaction_object_store, states, parent, authority, outcome="completed")
    await _fail_child(sessions, child_run_id)
    displays = RunDisplayStore(interaction_object_store)
    publisher = AsyncSubagentResultPublisher(sessions, displays, clock=lambda: NOW + timedelta(seconds=5))
    with capture_sql(sessions) as statements:
        entry = await publisher.publish(organization_id=ORGANIZATION_ID, child_run_id=child_run_id)
    _assert_database_work(f"publish_{delivery}", statements, 13)
    with capture_sql(sessions) as statements:
        replay = await publisher.publish(organization_id=ORGANIZATION_ID, child_run_id=child_run_id)
    _assert_database_work(f"publish_replay_{delivery}", statements, 11)
    assert replay == entry
    if delivery == "active":
        with capture_sql(sessions) as statements:
            message = await AsyncSubagentResultMaterializer(sessions, displays)(entry)
        _assert_database_work("materialize", statements, 8)
        assert child_run_id in message and "untrusted data" in message
        assert entry.target_run_id == parent.id
    else:
        reconciler = AsyncSubagentSuccessorReconciler(
            sessions,
            states,
            displays,
            bindings=ordinary_memory(sessions),
            clock=lambda: NOW + timedelta(seconds=6),
            lifecycle=test_lifecycle_writer(),
        )
        with capture_sql(sessions) as statements:
            if delivery == "scan":
                sweep = await reconciler.scan()
                assert sweep.completed == 1
            else:
                receipt = await reconciler.reconcile_thread(organization_id=ORGANIZATION_ID, thread_id=parent.thread_id)
                assert receipt.outcome == "run_accepted"
        _assert_database_work(delivery, statements, 30 if delivery == "scan" else 29)
        async with short_session(sessions) as database:
            thread = await database.get(ThreadRecord, parent.thread_id)
            successor = await database.get(RunRecord, thread.current_run_id)
            assert successor.parent_run_id == parent.id
            assert successor.input_kind == "async_subagent_result"
