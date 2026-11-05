from datetime import UTC, datetime, timedelta

import pytest
from a13n_harness.tool_execution import StoredToolExecution, ToolExecutionRecord
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import tool_executions
from a13n_harness_ui.storage.database import short_session, transaction
from a13n_harness_ui.storage.models import ToolExecutionHeadRecord
from a13n_harness_ui.storage.objects import ObjectKind
from a13n_harness_ui.storage.runtime import open_local_store
from anyio import Event, create_task_group, fail_after, sleep

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


def execution(identity="exec-a", thread_id="thread-a", *, terminal=True):
    return StoredToolExecution(
        ToolExecutionRecord(
            execution_id=identity,
            thread_id=thread_id,
            run_id="run-a",
            tool_call_id="call-a",
            tool_name="shell_exec",
            status="succeeded" if terminal else "running",
            completed_at=datetime.now(UTC) if terminal else None,
            result_available=terminal,
            foreground_released=True,
        ),
        "same result" if terminal else None,
    )


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        current = datetime.now(UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(tool_executions, "datetime", Clock)
    return Clock


@pytest.mark.parametrize("consume", ["checkpoint", "result", "never"])
async def test_retention_starts_at_consumption_and_keeps_independent_maximum(tmp_path, clock, consume):
    settings = StorageSettings(data_root=tmp_path)
    original = execution()
    clock.current = original.record.completed_at
    async with open_local_store(settings) as store:
        repo = store.tool_executions
        await repo.save(original)
        clock.current += timedelta(hours=12)
        assert len(await repo.pending("thread-a")) == 1
        assert (await repo.get("thread-a", "exec-a")).result == "same result"
        async with short_session(store.database.sessions) as session:
            row = await session.get(ToolExecutionHeadRecord, "exec-a")
            assert row.consumed_at is None
            assert row.expires_at == original.record.completed_at + timedelta(days=1)
        if consume == "checkpoint":
            async with transaction(store.database.sessions) as session:
                await tool_executions.consume_completions(session, "thread-a", ("exec-a",))
        elif consume == "result":
            await repo.acknowledge_delivery("thread-a", "exec-a")
        clock.current += timedelta(minutes=30)
        if consume != "never":
            # A repeated acknowledgement must not renew the first reread window.
            await repo.acknowledge_delivery("thread-a", "exec-a")
        assert await repo.prune() == 0
    async with open_local_store(settings) as store:
        repo = store.tool_executions
        clock.current += timedelta(minutes=31)
        retained = await repo.get("thread-a", "exec-a")
        if consume == "never":
            assert retained.record.result_available
            assert retained.result == "same result"
            clock.current = original.record.completed_at + timedelta(days=1)
        else:
            assert not retained.record.result_available and retained.result is None
        assert await repo.prune() == 1
        assert not (await repo.get("thread-a", "exec-a")).record.result_available
        assert not (await repo.list("thread-a", offset=0, limit=10))[0].result_available
        assert not [ref for ref in await store.objects.references() if ref.object_kind == ObjectKind.tool_execution]
        # Stale saves must not recreate a result or reset expiration.
        await repo.save(original)
        assert not (await repo.get("thread-a", "exec-a")).record.result_available
        assert not [ref for ref in await store.objects.references() if ref.object_kind == ObjectKind.tool_execution]


async def test_pruning_collects_superseded_and_unselected_publications_without_sharing_results(tmp_path, monkeypatch):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        repo = store.tool_executions
        running = execution(terminal=False)
        await repo.save(running)
        publish = store.objects.publish_model

        async def interrupted(**kwargs):
            await publish(**kwargs)
            raise RuntimeError("crash before head selection")

        monkeypatch.setattr(store.objects, "publish_model", interrupted)
        with pytest.raises(RuntimeError, match="crash"):
            await repo.save(execution())
        monkeypatch.setattr(store.objects, "publish_model", publish)
        assert await repo.prune() == 1
        assert (await repo.get("thread-a", "exec-a")).record.status == "running"
        await repo.save(execution())
        await repo.save(execution("exec-b", "thread-b"))
        assert await repo.prune() == 1
        async with transaction(store.database.sessions) as session:
            row = await session.get(ToolExecutionHeadRecord, "exec-a")
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        assert await repo.prune() == 1
        assert (await repo.get("thread-b", "exec-b")).result == "same result"
        assert (
            len([ref for ref in await store.objects.references() if ref.object_kind == ObjectKind.tool_execution]) == 1
        )


async def test_collection_waits_for_in_flight_result_read_across_stores(tmp_path, monkeypatch):
    settings = StorageSettings(data_root=tmp_path)
    async with open_local_store(settings) as reader, open_local_store(settings) as collector:
        await reader.tool_executions.save(execution())
        started, release, collected = Event(), Event(), Event()
        read = reader.objects.read_model
        results = []

        async def paused(reference, model):
            started.set()
            await release.wait()
            return await read(reference, model)

        monkeypatch.setattr(reader.objects, "read_model", paused)

        async def get():
            results.append(await reader.tool_executions.get("thread-a", "exec-a"))

        async def prune():
            assert await collector.tool_executions.prune() == 1
            collected.set()

        with fail_after(5):
            async with create_task_group() as group:
                group.start_soon(get)
                await started.wait()
                async with transaction(collector.database.sessions) as session:
                    row = await session.get(ToolExecutionHeadRecord, "exec-a")
                    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                group.start_soon(prune)
                await sleep(0.1)
                assert not collected.is_set()
                release.set()
        assert results[0].result == "same result"
        assert not (await collector.tool_executions.get("thread-a", "exec-a")).record.result_available


@pytest.mark.parametrize("checkpointed", [False, True])
async def test_completion_offer_has_one_app_owner_until_settlement_or_owner_exit(tmp_path, checkpointed):
    from a13n_harness_ui.tool_executions import ToolExecutions

    settings = StorageSettings(data_root=tmp_path)
    async with open_local_store(settings) as first, open_local_store(settings) as second:
        await first.tool_executions.save(execution())
        owners = [ToolExecutions(first.tool_executions), ToolExecutions(second.tool_executions)]
        offers = []

        async def deliver(thread_id, prompt, identities):
            offers.append(identities)
            return True

        for owner in owners:
            owner.deliver = deliver
        await owners[0]._deliver("thread-a")
        await owners[1]._deliver("thread-a")
        await owners[0]._deliver("thread-a")
        assert offers == [("exec-a",)]
        if checkpointed:
            async with transaction(first.database.sessions) as session:
                await tool_executions.consume_completions(session, "thread-a", ("exec-a",))
            owners[0].run_settled("thread-a")
            await owners[0]._deliver("thread-a")
        else:
            # Loss of the offer owner must not consume an uncheckpointed hint.
            owners[0].stop()
            await first.tool_executions.completion_owners.close()
        await owners[1]._deliver("thread-a")
        assert offers == [("exec-a",)] * (1 if checkpointed else 2)


async def test_deletion_blocks_retained_work_and_collects_only_deleted_family(tmp_path):
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.errors import StoreConflictError
    from a13n_harness_ui.storage.models import ToolCompletionSuppressionRecord

    from .test_app import _settings, _write_configuration

    path = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path) as app:
        app._tool_executions.stop()
        root = await app.create_thread()
        saved = await app._store.threads.get(root.thread_id)
        child = await app._store.threads.create(
            thread_id="thread-child",
            parent_thread_id=root.thread_id,
            configuration=saved.configuration,
            initial_state=saved.initial_state,
        )
        repo = app._store.tool_executions
        await repo.save(execution(thread_id=child.thread_id, terminal=False))
        with pytest.raises(StoreConflictError, match="tools to finish"):
            await app._store.threads.delete(
                root.thread_id,
                expected_version=1,
                active_execution_ids=frozenset(),
                busy_thread_ids=frozenset(),
            )
        await repo.save(execution(thread_id=child.thread_id))
        await repo.suppress(child.thread_id)
        await repo.save(execution("exec-b", "unrelated-thread"))
        await app._store.threads.delete(
            root.thread_id,
            expected_version=1,
            active_execution_ids=frozenset(),
            busy_thread_ids=frozenset(),
        )
        assert await repo.get(child.thread_id, "exec-a") is None
        async with short_session(app._store.database.sessions) as session:
            assert await session.get(ToolCompletionSuppressionRecord, child.thread_id) is None
        assert await repo.prune() == 2
        assert (await repo.get("unrelated-thread", "exec-b")).result == "same result"


async def test_successor_refreshes_suppression_cleared_by_another_app(tmp_path):
    from a13n_harness_ui.tool_executions import ToolExecutions

    settings = StorageSettings(data_root=tmp_path)
    async with open_local_store(settings) as first, open_local_store(settings) as second:
        owners = [ToolExecutions(first.tool_executions), ToolExecutions(second.tool_executions)]
        await owners[0].suppress("thread-a")
        await owners[1].restore()
        assert not owners[1].allows_completion("thread-a")
        await owners[0].user_input("thread-a")
        await first.tool_executions.save(execution())
        owners[0].stop()
        delivered = Event()
        offers = []

        async def deliver(thread_id, prompt, identities):
            offers.append(identities)
            delivered.set()
            return True

        owners[1].deliver = deliver
        with fail_after(5):
            async with create_task_group() as group:
                owners[1].start(group)
                group.start_soon(owners[1].maintain)
                await delivered.wait()
                owners[1].stop()
        assert offers == [("exec-a",)]
