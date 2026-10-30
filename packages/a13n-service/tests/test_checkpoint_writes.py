"""Checkpoint SQL shape, exact display reuse and commit-failure ownership on real storage."""

from dataclasses import replace

import pytest
from a13n_harness import HarnessState
from a13n_service.infra.db import transaction
from a13n_service.infra.outbox import OutboxRow
from a13n_service.runs import checkpoints
from a13n_service.runs.attempts import AttemptControl, LeaseLost
from a13n_service.runs.claim import claim
from a13n_service.runs.execute import _Attempt, _plan
from a13n_service.runs.schemas import Outcome, Pending, PendingCall
from a13n_service.runs.tables import AttemptRow, RunRow
from a13n_service.runs.usage import totals
from pydantic_ai.messages import ModelRequest, UserPromptPart
from sqlalchemy import select, update

from .test_runs_attempts import _statements
from .test_usage_deltas import delta

pytestmark = pytest.mark.anyio


@pytest.fixture
async def checkpoint_attempt(service, scripted_model, runs_kit):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.create_agent(service, scripted_model)
    await runs_kit.start_thread(
        service,
        agent,
        "hi",
        message_history=[
            {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "h" * 122880}]},
            {"kind": "response", "parts": [{"part_kind": "text", "content": "a" * 122880}]},
        ],
        options={"context": {"headers": {"x-checkpoint-test": "c" * 2048}}},
    )
    (lease,) = await claim(service.runtime, worker_id="checkpoint-test", worker_build="test", limit=1)
    plan = await _plan(service.runtime, lease)
    attempt = _Attempt(service.runtime, lease, AttemptControl(), plan)
    # Drive a safe boundary directly: the initial input is now in the exact state being committed.
    state = HarnessState.new(
        thread_id=lease.thread_id,
        message_history=[*plan.state.message_history, ModelRequest(parts=[UserPromptPart("hi")])],
    )
    attempt.offers.requested = True
    return attempt, state


def _assert_narrow_reads(statements):
    excluded = {
        "threads": ("message_history", "execution", "mcp_headers", "subagent", "labels"),
        "runs": (
            "revision_selection",
            "authority",
            "mcp_headers",
            "environment_mounts",
            "resume",
            "labels",
            "search_vector",
        ),
    }
    for table, names in excluded.items():
        locked = [s for s in statements if s.startswith("SELECT") and f"FROM {table} " in s and "FOR UPDATE" in s]
        assert len(locked) == 1, locked
        for name in names:
            assert f"{table}.{name} " not in locked[0], locked[0]
        # No deferred-column follow-up SELECT may undo the projection within this operation.
        for statement in statements:
            if statement.startswith("SELECT"):
                assert all(f"{table}.{name} " not in statement for name in names), statement


@pytest.mark.parametrize("status", ["completed", "waiting"])
async def test_final_checkpoint_writes_run_once_with_current_usage(checkpoint_attempt, service, status, monkeypatch):
    attempt, state = checkpoint_attempt
    # Charges enter through explicit INSERTs in this very checkpoint, before seal's totals query.
    attempt.usage.add(delta("checkpoint", 17, 1).records)
    pending = Pending(approvals=(), calls=(PendingCall(tool_call_id="call_lookup", tool_name="lookup", arguments={}),))
    outcome = (
        Outcome(status="completed", output="Done")
        if status == "completed"
        else Outcome(status="waiting", pending=pending)
    )
    from a13n_service.runs import execute as execution

    advance = execution.advance
    checkpoint_end = None

    async def after_checkpoint(*args):
        nonlocal checkpoint_end
        checkpoint_end = len(statements)
        await advance(*args)

    monkeypatch.setattr(execution, "advance", after_checkpoint)
    with _statements(service.runtime) as statements:
        await attempt._commit(state, cursors={}, snapshot=attempt._snapshot(), outcome=outcome)
    updates = [s for s in statements if s.startswith("UPDATE runs SET")]
    assert len(updates) == 1, updates
    assert all(f"{name}=" in updates[0] for name in ("checkpoint", "tail", "status", "usage_at_seal"))
    # advance() is a separate post-commit operation; inspect only through the final Run write.
    assert checkpoint_end is not None
    _assert_narrow_reads(statements[:checkpoint_end])
    async with transaction(service.runtime.storage) as session:
        run = await session.get_one(RunRow, attempt.lease.run_id)
        assert run.status == status and run.checkpoint["seq"] == 1
        assert (
            run.usage_at_seal
            == await totals(session, run.id)
            == {"requests": 1, "input_tokens": 17, "output_tokens": 0}
        )
        assert run.output == outcome.output
        assert run.pending == (pending.model_dump(mode="json") if status == "waiting" else None)
        ended = await session.get_one(AttemptRow, attempt.lease.attempt_id)
        assert ended.status == "succeeded"


async def test_unchanged_checkpoint_reuses_tail_without_get_and_cleanup_keeps_it(
    checkpoint_attempt, service, monkeypatch
):
    attempt, state = checkpoint_attempt
    snapshot = attempt._snapshot()
    await attempt._commit(state, cursors={}, snapshot=snapshot)
    first = attempt.committed
    assert first is not None
    writes = []
    put = service.runtime.objects.put

    async def record_put(key, data, *, content_type):
        writes.append(key)
        return await put(key, data, content_type=content_type)

    async def no_get(key):
        raise AssertionError("Reuse must not read an object")

    with monkeypatch.context() as patch:
        patch.setattr(service.runtime.objects, "put", record_put)
        patch.setattr(service.runtime.objects, "get", no_get)
        with _statements(service.runtime) as statements:
            await attempt._commit(state, cursors={}, snapshot=snapshot)
    _assert_narrow_reads(statements)
    second = attempt.committed
    assert second is not None and second.tail == first.tail and second.state != first.state
    assert len(writes) == 1 and "/state/" in writes[0]
    async with transaction(service.runtime.storage) as session:
        intents = list(await session.scalars(select(OutboxRow).where(OutboxRow.kind == checkpoints.CLEANUP)))
        assert any(first.state.key in row.payload.get("keys", []) for row in intents)
        assert all(first.tail.key not in row.payload.get("keys", []) for row in intents)
    from .test_checkpoint_cleanup import _deliver

    await _deliver(service.runtime)
    assert await service.runtime.objects.get(first.state.key) is None
    assert await checkpoints.load_tail(service.runtime.objects, second.tail) is not None
    assert await checkpoints.load_state(service.runtime.objects, second.state) is not None


async def test_failed_commit_retains_tail_cache_and_retry_uploads_new_tail(checkpoint_attempt, service, monkeypatch):
    attempt, state = checkpoint_attempt
    snapshot = attempt._snapshot()
    await attempt._commit(state, cursors={}, snapshot=snapshot)
    before, before_bytes = attempt.committed, attempt.last_committed_tail_bytes
    changed = replace(snapshot, tail=snapshot.tail.model_copy(update={"resume_after": "1-1"}))
    commit = checkpoints.commit

    async def fail_after_write(*args, **kwargs):
        await commit(*args, **kwargs)
        await args[0].flush()
        raise RuntimeError("Fail after SQL flush")

    with monkeypatch.context() as patch:
        patch.setattr(checkpoints, "commit", fail_after_write)
        with pytest.raises(RuntimeError, match="after SQL flush"):
            await attempt._commit(state, cursors={}, snapshot=changed)
    assert (attempt.committed, attempt.last_committed_tail_bytes) == (before, before_bytes)
    async with transaction(service.runtime.storage) as session:
        run = await session.get_one(RunRow, attempt.lease.run_id)
        assert checkpoints.Committed.of(run) == before
    writes = []
    put = service.runtime.objects.put

    async def record_put(key, data, *, content_type):
        writes.append(key)
        return await put(key, data, content_type=content_type)

    with monkeypatch.context() as patch:
        patch.setattr(service.runtime.objects, "put", record_put)
        await attempt._commit(state, cursors={}, snapshot=changed)
    assert len([key for key in writes if "/tail/" in key]) == 1
    assert attempt.committed.tail != before.tail
    assert attempt.last_committed_tail_bytes != before_bytes


async def test_narrow_checkpoint_lock_still_refuses_an_expired_lease(checkpoint_attempt, service):
    attempt, state = checkpoint_attempt
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(AttemptRow)
            .where(AttemptRow.id == attempt.lease.attempt_id)
            .values(lease_expires_at=AttemptRow.created_at)
        )
    with pytest.raises(LeaseLost):
        await attempt._commit(state, cursors={}, snapshot=attempt._snapshot())
    assert attempt.committed is None and attempt.last_committed_tail_bytes is None
    async with transaction(service.runtime.storage) as session:
        assert (await session.get_one(RunRow, attempt.lease.run_id)).checkpoint is None


async def test_restored_attempt_starts_without_a_tail_comparison_cache(checkpoint_attempt, service):
    attempt, state = checkpoint_attempt
    await attempt._commit(state, cursors={}, snapshot=attempt._snapshot())
    previous = attempt.committed
    plan = await _plan(service.runtime, attempt.lease)
    restored = _Attempt(service.runtime, attempt.lease, AttemptControl(), plan)
    assert restored.committed == previous and restored.last_committed_tail_bytes is None
    await restored._commit(state, cursors={}, snapshot=attempt._snapshot())
    assert restored.committed.tail.key != previous.tail.key
    assert restored.last_committed_tail_bytes == attempt.last_committed_tail_bytes
