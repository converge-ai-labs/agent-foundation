"""Reused preparation facts must not replace final child and inbox arbitration."""

from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_harness.capabilities import AsyncDelegateRequest, AsyncResumeRequest
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.interactions.attempts import AttemptAuthorityError
from a13n_service.interactions.control_models import ThreadInboxRecord
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, ThreadRecord
from a13n_service.interactions.scheduling import AttemptScheduler
from a13n_service.run_stream import RunDisplayStore
from a13n_service.storage import short_session, transaction
from a13n_service.subagents import (
    AsyncSubagentResultError,
    AsyncSubagentResultMaterializer,
    AsyncSubagentResultPublisher,
    AsyncSubagentSuccessorReconciler,
    ChildRunAcceptanceError,
)
from a13n_service.subagents.execution_store import SubagentOperatorError
from a13n_service.subagents.models import ChildRunRelationshipRecord
from a13n_service.subagents.result_payload import read_async_subagent_result_authority
from sqlalchemy import delete, func, select

from tests.lifecycle_support import test_lifecycle_writer
from tests.memory.selection_support import ordinary_memory

from .conftest import NOW, ORGANIZATION_ID, USER_ID
from .test_attempt_execution import _authority, _worker
from .test_subagent_acceptance import _complete_run
from .test_subagent_operator import _operator, _plan, _run
from .test_subagent_results import _accept_child, _fail_child
from .test_subagent_successors import _accept_another_child, _seal_parent

pytestmark = pytest.mark.anyio


async def _completed_child(sessions, objects, operator, plan, states):
    delegated = await operator.delegate(plan, AsyncDelegateRequest(subagent_name="researcher", prompt="research"))
    claim = await AttemptScheduler(
        sessions,
        clock=lambda: NOW + timedelta(seconds=3),
        token_factory=lambda: "child-lease",
        attempt_id_factory=lambda: "rat_6767676767676767",
        lifecycle=test_lifecycle_writer(),
    ).claim(delegated.child_run_id, _worker())
    assert claim is not None
    await _complete_run(sessions, objects, states, await _run(sessions, delegated.child_run_id), _authority(claim))
    return delegated


@pytest.mark.parametrize("operation", ["delegate", "resume"])
async def test_parent_lease_expiry_during_publication_rejects_observed_admission(
    interaction_sessions, interaction_object_store, monkeypatch, operation
):
    sessions = interaction_sessions
    operator, context, plan, states, _, authority = await _operator(sessions, interaction_object_store)
    delegated = None
    if operation == "resume":
        delegated = await _completed_child(sessions, interaction_object_store, operator, plan, states)
    publish = states.create

    async def expire_after_publication(*args, **kwargs):
        stored = await publish(*args, **kwargs)
        async with transaction(sessions) as database:
            attempt = await database.get(RunAttemptRecord, authority.current_context.run_attempt_id)
            attempt.lease_expires_at = NOW
        return stored

    monkeypatch.setattr(states, "create", expire_after_publication)
    with pytest.raises(AttemptAuthorityError):
        if delegated is None:
            await operator.delegate(plan, AsyncDelegateRequest(subagent_name="researcher", prompt="research"))
        else:
            await operator.resume(
                _plan(context), AsyncResumeRequest(execution_id=delegated.execution_id, prompt="next")
            )
    async with short_session(sessions) as database:
        assert await database.scalar(select(func.count()).select_from(ChildRunRelationshipRecord)) == (
            0 if delegated is None else 1
        )
        assert await database.scalar(select(func.count()).select_from(RunRecord)) == (1 if delegated is None else 2)
        assert await database.scalar(select(func.count()).select_from(ThreadRecord)) == (1 if delegated is None else 2)


@pytest.mark.parametrize("mutation", ["version", "state_digest"])
async def test_resume_revalidates_observed_child_at_commit(
    interaction_sessions, interaction_object_store, monkeypatch, mutation
):
    sessions = interaction_sessions
    operator, context, plan, states, _, _ = await _operator(sessions, interaction_object_store)
    delegated = await _completed_child(sessions, interaction_object_store, operator, plan, states)
    if mutation == "version":
        publish = states.create

        async def advance_after_publication(*args, **kwargs):
            stored = await publish(*args, **kwargs)
            async with transaction(sessions) as database:
                thread = await database.get(ThreadRecord, delegated.thread_id)
                thread.version += 1
            return stored

        monkeypatch.setattr(states, "create", advance_after_publication)
    else:
        prepare = operator._admission_preparer.prepare_resume

        async def corrupt_observed_digest(*args, **kwargs):
            admission = await prepare(*args, **kwargs)
            # The sealed database row is immutable; a forwarded object must still match its digest.
            return replace(admission, source_state=replace(admission.source_state, digest_sha256="f" * 64))

        monkeypatch.setattr(operator._admission_preparer, "prepare_resume", corrupt_observed_digest)
    with pytest.raises(ChildRunAcceptanceError) as error:
        await operator.resume(_plan(context), AsyncResumeRequest(execution_id=delegated.execution_id, prompt="next"))
    assert error.value.code == (
        "child_run_resume_source_conflict" if mutation == "version" else "child_run_resume_state_conflict"
    )
    async with short_session(sessions) as database:
        assert await database.scalar(select(func.count()).select_from(ChildRunRelationshipRecord)) == 1
        assert await database.scalar(select(func.count()).select_from(RunRecord)) == 2
        thread = await database.get(ThreadRecord, delegated.thread_id)
        assert thread.current_run_id == delegated.child_run_id


@pytest.mark.parametrize("mismatch", ["attempt", "thread"])
async def test_forwarded_parent_observation_cannot_change_scope(
    interaction_sessions, interaction_object_store, monkeypatch, mismatch
):
    operator, _, plan, _, _, _ = await _operator(interaction_sessions, interaction_object_store)
    prepare = operator._admission_preparer.prepare_delegate

    async def retarget(*args, **kwargs):
        admission = await prepare(*args, **kwargs)
        parent = admission.parent
        if mismatch == "attempt":
            parent = replace(parent, authority=replace(parent.authority, attempt_number=99))
        else:
            parent = replace(parent, thread=parent.thread.model_copy(update={"id": "thread-other"}))
        return replace(admission, parent=parent)

    monkeypatch.setattr(operator._admission_preparer, "prepare_delegate", retarget)
    with pytest.raises(SubagentOperatorError, match="changed scope"):
        await operator.delegate(plan, AsyncDelegateRequest(subagent_name="researcher", prompt="research"))
    async with short_session(interaction_sessions) as database:
        assert await database.scalar(select(func.count()).select_from(ChildRunRelationshipRecord)) == 0


@pytest.mark.parametrize("mutation", ["relationship", "grants"])
async def test_materialization_rechecks_provenance_but_keeps_operation_authorization(
    interaction_sessions, interaction_object_store, monkeypatch, mutation
):
    sessions = interaction_sessions
    _, _, _, child_run_id = await _accept_child(sessions, interaction_object_store)
    await _fail_child(sessions, child_run_id)
    displays = RunDisplayStore(interaction_object_store)
    entry = await AsyncSubagentResultPublisher(sessions, displays).publish(
        organization_id=ORGANIZATION_ID, child_run_id=child_run_id
    )
    from a13n_service.subagents import result_delivery

    load_item = result_delivery.load_async_subagent_terminal_item

    async def mutate_during_result_read(*args, **kwargs):
        item = await load_item(*args, **kwargs)
        async with transaction(sessions) as database:
            if mutation == "relationship":
                relationship = await database.get(ChildRunRelationshipRecord, entry.async_subagent_relationship_id)
                relationship.subagent_name = "different-child"
            else:
                await database.execute(delete(RoleBindingRecord).where(RoleBindingRecord.principal_id == USER_ID))
        return item

    monkeypatch.setattr(result_delivery, "load_async_subagent_terminal_item", mutate_during_result_read)
    materialize = AsyncSubagentResultMaterializer(sessions, displays)
    if mutation == "relationship":
        with pytest.raises(AsyncSubagentResultError, match="durable authority is incomplete"):
            await materialize(entry)
    else:
        assert child_run_id in await materialize(entry)
        with pytest.raises(AsyncSubagentResultError, match="no longer authorized"):
            await materialize(entry)


@pytest.mark.parametrize("field", ["id", "organization_id", "session_id"])
async def test_result_source_validates_the_forwarded_origin(interaction_sessions, interaction_object_store, field):
    sessions = interaction_sessions
    _, parent, _, child_run_id = await _accept_child(sessions, interaction_object_store)
    await _fail_child(sessions, child_run_id)
    entry = await AsyncSubagentResultPublisher(sessions, RunDisplayStore(interaction_object_store)).publish(
        organization_id=ORGANIZATION_ID, child_run_id=child_run_id
    )
    async with short_session(sessions) as database:
        with pytest.raises(AsyncSubagentResultError, match="relationship authority is incomplete"):
            await read_async_subagent_result_authority(
                database, entry, parent=parent.model_copy(update={field: "other"})
            )


@pytest.mark.parametrize("unavailable", [None, "authorization", "payload"])
async def test_scan_finalizes_bound_expiry_independently_of_successor_preparation(
    interaction_sessions, interaction_object_store, unavailable
):
    sessions = interaction_sessions
    states, parent, authority, first_child = await _accept_child(sessions, interaction_object_store)
    next_child = await _accept_another_child(sessions, interaction_object_store, states, parent, authority)
    await _seal_parent(sessions, interaction_object_store, states, parent, authority, outcome="completed")
    publisher = AsyncSubagentResultPublisher(
        sessions, RunDisplayStore(interaction_object_store), clock=lambda: NOW + timedelta(seconds=5)
    )
    await _fail_child(sessions, first_child)
    await _fail_child(sessions, next_child)
    first = await publisher.publish(organization_id=ORGANIZATION_ID, child_run_id=first_child)
    second = await publisher.publish(organization_id=ORGANIZATION_ID, child_run_id=next_child)
    async with transaction(sessions) as database:
        expired = await database.get(ThreadInboxRecord, first.id)
        expired.target_run_id = parent.id
        expired.expires_at = NOW + timedelta(seconds=5)
        if unavailable == "authorization":
            await database.execute(delete(RoleBindingRecord).where(RoleBindingRecord.principal_id == USER_ID))
        elif unavailable == "payload":
            candidate = await database.get(ThreadInboxRecord, second.id)
            candidate.payload_json = {**candidate.payload_json, "subagent_name": "different-child"}
    sweep = await AsyncSubagentSuccessorReconciler(
        sessions,
        states,
        RunDisplayStore(interaction_object_store),
        bindings=ordinary_memory(sessions),
        clock=lambda: NOW + timedelta(seconds=6),
        lifecycle=test_lifecycle_writer(),
    ).scan()
    assert (sweep.examined, sweep.completed, sweep.deferred) == ((1, 1, 0) if unavailable is None else (1, 0, 1))
    async with short_session(sessions) as database:
        expired = await database.get(ThreadInboxRecord, first.id)
        consumed = await database.get(ThreadInboxRecord, second.id)
        thread = await database.get(ThreadRecord, parent.thread_id)
        assert expired.status == "expired" and expired.target_run_id is None
        if unavailable is None:
            assert consumed.status == "consumed" and consumed.consumed_by_run_id == thread.current_run_id
            assert (thread.pending_count, thread.pending_bytes) == (0, 0)
        else:
            assert consumed.status == "pending" and thread.current_run_id == parent.id
            assert thread.pending_count == 1
