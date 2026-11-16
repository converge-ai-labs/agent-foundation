from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from a13n_harness.envelopes import Envelope, ExecutionRef, Sender
from a13n_harness.tool_execution import StoredToolExecution, ToolExecutionRecord
from a13n_harness_ui.configuration.models import BotConfiguration
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage.database import transaction
from a13n_harness_ui.storage.runtime import open_local_store
from a13n_harness_ui.storage.thread_inbox import append, consume
from a13n_harness_ui.thread_inbox import root_condition

pytestmark = pytest.mark.anyio


def message(identity="input-a", *, delivery="steer"):
    return Envelope(
        id=identity,
        kind="agent_message",
        sender=Sender(kind="agent", thread_id="source"),
        text="Verified result",
        delivery=delivery,
    )


@pytest.mark.parametrize(
    "last,continuation,expected",
    [
        (None, None, "idle"),
        (None, "saved", "paused"),
        ("completed", "saved", "idle"),
        ("failed", "saved", "paused"),
        ("cancelled", "saved", "paused"),
        ("preparing", "saved", "paused"),
        ("running", "saved", "paused"),
    ],
)
async def test_root_condition_requires_trustworthy_latest_execution(last, continuation, expected):
    thread = SimpleNamespace(
        archived=False,
        memory_scope=None,
        continuation=continuation,
        last_execution=None if last is None else SimpleNamespace(status=last),
    )
    assert root_condition(thread, None, deferred=False) == expected
    assert root_condition(thread, None, deferred=True) == "waiting"
    assert root_condition(thread, SimpleNamespace(status="preparing"), deferred=False) == "preparing"
    assert root_condition(thread, SimpleNamespace(status="running"), deferred=False) == "active"
    assert root_condition(thread, SimpleNamespace(status="running"), deferred=False, cancelling=True) == "paused"
    thread.archived = True
    assert root_condition(thread, None, deferred=False) == "archived"


async def test_pending_inputs_survive_restart_and_consumption_is_transactional(tmp_path):
    settings = StorageSettings(data_root=tmp_path)
    async with open_local_store(settings) as store:
        await store.thread_inbox.put("thread-a", message())
        await store.thread_inbox.put("thread-a", message())
        with pytest.raises(ValueError):
            async with transaction(store.database.sessions) as session:
                await consume(session, "thread-a", ("input-a",))
                raise ValueError("checkpoint conflict")
        assert len(await store.thread_inbox.pending("thread-a")) == 1
    async with open_local_store(settings) as store:
        assert (await store.thread_inbox.pending("thread-a"))[0].envelope == message()
        async with transaction(store.database.sessions) as session:
            await consume(session, "other-thread", ("input-a",))
        assert len(await store.thread_inbox.pending("thread-a")) == 1
        async with transaction(store.database.sessions) as session:
            await consume(session, "thread-a", ("input-a",))
        assert await store.thread_inbox.pending("thread-a") == ()


async def test_order_exclusion_and_pending_dedupe(tmp_path):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        now = datetime.now(UTC)
        async with transaction(store.database.sessions) as session:
            await append(session, "thread-a", message("later"), created_at=now + timedelta(seconds=1))
            await append(session, "thread-a", message("first"), created_at=now, dedupe_key="event")
            identity = await append(session, "thread-a", message("duplicate"), dedupe_key="event")
        assert identity == "first"
        assert [row.envelope.id for row in await store.thread_inbox.pending("thread-a")] == ["first", "later"]
        assert [row.envelope.id for row in await store.thread_inbox.pending("thread-a", exclude=("first",))] == [
            "later"
        ]
        assert await store.thread_inbox.threads(limit=1) == ("thread-a",)


async def test_tool_obligation_is_created_once_with_terminal_fact(tmp_path):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        record = ToolExecutionRecord(
            execution_id="exec-one",
            thread_id="thread-a",
            run_id="run-a",
            tool_call_id="call-one",
            tool_name="shell_exec",
            status="running",
            foreground_released=True,
        )
        await store.tool_executions.save(StoredToolExecution(record))
        assert await store.thread_inbox.pending("thread-a") == ()
        record = record.model_copy(
            update={"status": "succeeded", "completed_at": datetime.now(UTC), "result_available": True}
        )
        await store.tool_executions.save(StoredToolExecution(record, "finished"))
        pending = await store.thread_inbox.pending("thread-a")
        assert len(pending) == 1 and pending[0].envelope.execution.execution_id == "exec-one"
        async with transaction(store.database.sessions) as session:
            await consume(session, "thread-a", (pending[0].envelope.id,))
        await store.tool_executions.save(StoredToolExecution(record, "finished"))
        await store.tool_executions.release_foreground("thread-a", "exec-one")
        assert await store.thread_inbox.pending("thread-a") == ()


async def test_result_retrieval_withdraws_notice_without_losing_retained_result(tmp_path):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        record = ToolExecutionRecord(
            execution_id="exec-one",
            thread_id="thread-a",
            run_id="run-a",
            tool_call_id="call-one",
            tool_name="shell_exec",
            status="succeeded",
            foreground_released=True,
            result_available=True,
            completed_at=datetime.now(UTC),
        )
        await store.tool_executions.save(StoredToolExecution(record, "finished"))
        await store.tool_executions.acknowledge_delivery("thread-a", "exec-one")
        assert await store.thread_inbox.pending("thread-a") == ()
        assert (await store.tool_executions.get("thread-a", "exec-one")).result == "finished"


async def test_heartbeat_coalescing_and_result_outside_bot_budget(tmp_path):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        now = datetime.now(UTC)
        policy = BotConfiguration(enabled=True, heartbeat_enabled=True)
        await store.thread_inbox.heartbeat(policy=policy, now=now, text="Read HEARTBEAT.md")
        await store.thread_inbox.heartbeat(policy=policy, now=now + timedelta(days=1), text="Read HEARTBEAT.md")
        first = await store.thread_inbox.pending("thread_bot")
        await store.thread_inbox.heartbeat(policy=policy, now=now + timedelta(days=2), text="Read HEARTBEAT.md")
        assert await store.thread_inbox.pending("thread_bot") == first
        large = Envelope(
            id="result-large",
            kind="result",
            sender=Sender(kind="agent"),
            text="x" * 80000,
            execution=ExecutionRef(execution_id="execution-a", kind="agent", name="worker", state="succeeded"),
        )
        await store.thread_inbox.put("thread_bot", large)
        batch = await store.thread_inbox.bot_batch(await store.thread_inbox.pending("thread_bot"))
        assert {row.envelope.id for row in batch} == {first[0].envelope.id, "result-large"}
        await store.thread_inbox.heartbeat(
            policy=policy.model_copy(update={"heartbeat_enabled": False}), now=now, text=""
        )
        assert [row.envelope.id for row in await store.thread_inbox.pending("thread_bot")] == ["result-large"]


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled", "lost"])
async def test_tool_notice_contains_status_not_private_payload_and_skips_inline_or_retrieved_calls(tmp_path, status):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        for released, acknowledged, expected in ((False, False, 0), (True, True, 0), (True, False, 1)):
            record = ToolExecutionRecord(
                execution_id=f"exec-{released}-{acknowledged}",
                thread_id="thread-a",
                run_id="run-a",
                tool_call_id="call-a",
                tool_name="lookup",
                status=status,
                foreground_released=released,
                delivery_acknowledged=acknowledged,
                result_available=status == "succeeded",
                error="PRIVATE failure" if status != "succeeded" else None,
            )
            await store.tool_executions.save(StoredToolExecution(record, "PRIVATE result"))
            rows = await store.thread_inbox.pending("thread-a")
            assert len(rows) == expected
            if rows:
                envelope = rows[0].envelope
                assert envelope.kind == "result" and envelope.execution.state == status
                assert "PRIVATE" not in envelope.text
