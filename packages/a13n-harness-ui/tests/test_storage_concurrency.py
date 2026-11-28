from __future__ import annotations

from functools import partial
from pathlib import Path

import pytest
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import database as database_module
from a13n_harness_ui.storage.database import open_database, short_session, transaction
from a13n_harness_ui.storage.repositories import ProjectModelPreferenceRepository
from anyio import CancelScope, Event, create_task_group, fail_after, move_on_after, sleep, wait_all_tasks_blocked
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


async def test_child_resume_has_one_successor_across_database_connections(tmp_path: Path) -> None:
    from a13n_harness_ui.errors import StoreConflictError
    from a13n_harness_ui.storage import ObjectKind, ObjectRef, open_local_store
    from a13n_harness_ui.storage.repositories import ChildExecutionRepository

    from .test_thread_repository import _configuration, _initial

    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        await store.threads.create(thread_id="parent", configuration=_configuration(), initial_state=_initial())
        await store.threads.create(
            thread_id="child", parent_thread_id="parent", configuration=_configuration(), initial_state=_initial()
        )
        composition = ObjectRef(
            object_kind=ObjectKind.run_composition, object_schema_version="1", logical_digest="2" * 64
        )
        checkpoint = ObjectRef(
            object_kind=ObjectKind.child_checkpoint, object_schema_version="1", logical_digest="3" * 64
        )
        await store.child_executions.create(
            execution_id="original",
            parent_thread_id="parent",
            child_thread_id="child",
            child_run_id="run-original",
            run_composition=composition,
        )
        previous = await store.child_executions.finish(
            execution_id="original", status="succeeded", expected_checkpoint=None, checkpoint=checkpoint
        )
        accepted, rejected = [], []

        async def resume(repository, execution_id):
            try:
                accepted.append(
                    await repository.resume(
                        previous_execution_id="original",
                        execution_id=execution_id,
                        child_run_id=f"run-{execution_id}",
                        run_composition=composition,
                    )
                )
            except StoreConflictError as error:
                rejected.append(error)

        async with open_database(store.layout.database, store.settings) as other:
            async with create_task_group() as tasks:
                tasks.start_soon(resume, store.child_executions, "first")
                tasks.start_soon(resume, ChildExecutionRepository(other.sessions), "second")
        assert len(accepted) == len(rejected) == 1
        winner = accepted[0]
        assert winner.resumed_from == "original" and winner.segment_index == 1
        assert rejected[0].code == "child_execution_already_resumed"
        assert winner.execution_id in str(rejected[0])
        assert await store.child_executions.get("original") == previous
        assert await store.child_executions.get("second" if winner.execution_id == "first" else "first") is None
        await resume(store.child_executions, "retry")
        assert len(accepted) == 1 and len(rejected) == 2
        assert rejected[1].code == "child_execution_already_resumed"
        assert await store.child_executions.get("retry") is None


async def test_pending_writers_leave_connections_available_for_reads(tmp_path: Path) -> None:
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = ProjectModelPreferenceRepository(database.sessions)
        pool = database.engine.pool
        assert isinstance(pool, AsyncAdaptedQueuePool)
        async with create_task_group() as tasks:
            # Hold the writer only to model a slow transaction deterministically.
            async with transaction(database.sessions):
                for index in range(32):
                    tasks.start_soon(repository.set, f"project-{index}", "model")
                await wait_all_tasks_blocked()
                assert pool.checkedout() == 1
                with fail_after(1):
                    assert await repository.get("not-written") is None
        assert pool.checkedout() == 0
        for index in range(32):
            assert await repository.get(f"project-{index}") == "model"


async def test_cancelled_writer_exits_before_write_lock_is_released(tmp_path: Path) -> None:
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        entered, finished = Event(), Event()
        cancelled = CancelScope()
        body_ran = False

        async def waiter() -> None:
            nonlocal body_ran
            with cancelled:
                entered.set()
                async with transaction(database.sessions) as session:
                    body_ran = True
                    await session.execute(text("INSERT INTO project_model_preference VALUES ('cancelled', 'model')"))
            finished.set()

        async with create_task_group() as tasks:
            async with transaction(database.sessions):
                tasks.start_soon(waiter)
                await entered.wait()
                await wait_all_tasks_blocked()
                cancelled.cancel()
                with move_on_after(5) as deadline:
                    await finished.wait()
                exited_while_blocked = not deadline.cancel_called
        assert exited_while_blocked
        assert not body_ran
        assert await ProjectModelPreferenceRepository(database.sessions).get("cancelled") is None
        pool = database.engine.pool
        assert isinstance(pool, AsyncAdaptedQueuePool)
        assert pool.checkedout() == 0


@pytest.mark.parametrize("scope_kind", ["read", "write"])
async def test_pool_checkout_can_be_cancelled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope_kind: str) -> None:
    monkeypatch.setattr(database_module, "_POOL_SIZE", 1)
    monkeypatch.setattr(database_module, "_MAX_OVERFLOW", 0)
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        entered, finished = Event(), Event()
        cancelled = CancelScope()
        query_ran = False

        async def waiter() -> None:
            nonlocal query_ran
            with cancelled:
                entered.set()
                scope = transaction if scope_kind == "write" else short_session
                async with scope(database.sessions) as session:
                    await session.execute(text("SELECT 1"))
                    query_ran = True
            finished.set()

        async with create_task_group() as tasks:
            async with short_session(database.sessions) as held:
                await held.execute(text("SELECT 1"))
                tasks.start_soon(waiter)
                await entered.wait()
                await wait_all_tasks_blocked()
                cancelled.cancel()
                with move_on_after(5) as deadline:
                    await finished.wait()
                exited_while_blocked = not deadline.cancel_called
        assert exited_while_blocked
        assert not query_ran
        # A cancelled checkout must release writer admission as well as the session.
        await ProjectModelPreferenceRepository(database.sessions).set("after-cancellation", "model")
        pool = database.engine.pool
        assert isinstance(pool, AsyncAdaptedQueuePool)
        assert pool.checkedout() == 0


@pytest.mark.parametrize("scope_kind", ["read", "write"])
async def test_admitted_database_work_returns_connection_before_cancellation(tmp_path: Path, scope_kind: str) -> None:
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        completed = False
        with CancelScope() as cancelled:
            scope = transaction if scope_kind == "write" else short_session
            async with scope(database.sessions) as session:
                await session.execute(text("SELECT 1"))
                cancelled.cancel()
                # Even repeated SQL awaits finish before an outer cancellation
                # can interrupt aiosqlite's connection invalidation or rollback.
                for _ in range(3):
                    assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
                completed = True
        assert completed
        pool = database.engine.pool
        assert isinstance(pool, AsyncAdaptedQueuePool)
        assert pool.checkedout() == 0
        await ProjectModelPreferenceRepository(database.sessions).set("after-cancellation", "model")


async def test_read_burst_waits_outside_pool_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(database_module, "_POOL_SIZE", 2)
    monkeypatch.setattr(database_module, "_MAX_OVERFLOW", 0)
    monkeypatch.setattr(database_module, "create_async_engine", partial(create_async_engine, pool_timeout=0.01))
    async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)) as database:
        repository = ProjectModelPreferenceRepository(database.sessions)
        async with create_task_group() as tasks:
            async with short_session(database.sessions) as first, short_session(database.sessions) as second:
                await first.execute(text("SELECT 1"))
                await second.execute(text("SELECT 1"))
                for index in range(32):
                    tasks.start_soon(repository.get, f"project-{index}")
                # Wait longer than the deliberately tiny SQLAlchemy pool timeout.
                await wait_all_tasks_blocked()
                await sleep(0.05)
        pool = database.engine.pool
        assert isinstance(pool, AsyncAdaptedQueuePool)
        assert pool.checkedout() == 0
