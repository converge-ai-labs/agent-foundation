"""Small work projections retain identity, bounds, and repair semantics."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_harness import HarnessState
from a13n_harness.capabilities.working_state import (
    WORKING_STATE_CAPABILITY_ID,
    ProviderTaskCursor,
    Task,
    TaskState,
    WorkingState,
)
from a13n_harness.state import AgentContextStateSnapshot, CapabilityState
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import ObjectKind, ObjectRef, StoredThreadInitialState, open_local_store
from a13n_harness_ui.storage.contracts import ThreadReadModel
from a13n_harness_ui.storage.database import short_session, transaction
from a13n_harness_ui.storage.models import ThreadWorkRecord
from a13n_harness_ui.storage.work import WorkData
from a13n_harness_ui.surfaces import NotePage, TaskPage
from a13n_harness_ui.thread_work import WorkSummary, build_work_projection
from a13n_stream_protocol import DisplayPosition, DisplayScope, DisplaySnapshot, Producer
from sqlalchemy import delete, event

from .test_thread_repository import _configuration, _initial

pytestmark = pytest.mark.anyio


def continuation(character: str) -> ObjectRef:
    return ObjectRef(object_kind=ObjectKind.continuation, object_schema_version="1", logical_digest=character * 64)


def state(thread_id: str, working: WorkingState) -> HarnessState:
    return HarnessState.new(
        thread_id=thread_id,
        agent_context_state=AgentContextStateSnapshot(
            entries={WORKING_STATE_CAPABILITY_ID: CapabilityState(version="1", data=working.model_dump(mode="json"))}
        ),
    )


async def test_work_projection_binding_repair_and_summary_column_selection(tmp_path: Path, monkeypatch) -> None:
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        thread = await store.threads.create(
            thread_id="thr_work", configuration=_configuration(), initial_state=_initial()
        )
        first, second = continuation("a"), continuation("b")
        before = await store.threads.select_continuation(
            thread_id=thread.thread_id, expected=None, replacement=first, read_model=ThreadReadModel()
        )
        assert await store.work.missing() == ((thread.thread_id, first),)
        old = WorkData("old summary", "old tasks", "old notes")
        assert await store.work.publish(thread.thread_id, first, old)
        assert await store.work.missing() == ()
        statements = []

        def observe(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("SELECT thread_work."):
                statements.append(statement)

        event.listen(store.database.engine.sync_engine, "before_cursor_execute", observe)
        try:
            assert await store.work.read(thread.thread_id, first) == WorkData(old.summary_json)
            assert "tasks_json" not in statements[-1] and "notes_json" not in statements[-1]
            assert await store.work.read(thread.thread_id, first, ("notes",)) == WorkData(
                old.summary_json, notes_json=old.notes_json
            )
            assert "tasks_json" not in statements[-1]
            assert await store.work.read(thread.thread_id, first, ("tasks", "notes")) == old
        finally:
            event.remove(store.database.engine.sync_engine, "before_cursor_execute", observe)
        assert (await store.threads.get(thread.thread_id)) == before
        # A new head makes the previous projection unavailable, without loading either object.
        selected = await store.threads.select_continuation(
            thread_id=thread.thread_id, expected=first, replacement=second, read_model=ThreadReadModel()
        )
        assert await store.work.read(thread.thread_id, second) is None
        assert not await store.work.publish(thread.thread_id, first, old)
        assert await store.work.missing() == ((thread.thread_id, second),)
        assert await store.work.publish(thread.thread_id, second, WorkData("new", "tasks", "notes"))
        assert (await store.threads.get(thread.thread_id)) == selected
        from a13n_harness_ui.storage import work

        monkeypatch.setattr(work, "WORK_VERSION", work.WORK_VERSION + 1)
        assert await store.work.read(thread.thread_id, second) is None
        assert await store.work.missing() == ((thread.thread_id, second),)
        assert await store.work.publish(thread.thread_id, second, old)
        assert await store.work.read(thread.thread_id, second, ("tasks", "notes")) == old
        async with transaction(store.database.sessions) as session:
            await session.execute(delete(ThreadWorkRecord).where(ThreadWorkRecord.thread_id == thread.thread_id))
        assert await store.work.read(thread.thread_id, second) is None


async def test_work_repair_initial_state_restart_and_unreadable_row(tmp_path: Path) -> None:
    settings = StorageSettings(data_root=tmp_path)
    tasks = TaskState(
        next_task_sequence=301,
        tasks={
            f"task-{index}": Task(
                id=f"task-{index}",
                version=1,
                subject=f"Task {index}",
                description="Retained",
                status="in_progress" if index == 300 else "completed",
            )
            for index in range(1, 301)
        },
    )
    working = WorkingState(tasks=tasks, notes={f"note-{index:03}": "n" * 4096 for index in range(100)})
    async with open_local_store(settings) as store:
        saved = await store.objects.publish_model(
            object_kind=ObjectKind.thread_initial_state,
            value=StoredThreadInitialState(harness_state=state("thr_work", working), created_at=datetime.now(UTC)),
        )
        # An unreadable row must not prevent the next valid row from being repaired.
        await store.threads.create(thread_id="thr_broken", configuration=_configuration(), initial_state=_initial())
        before = await store.threads.create(
            thread_id="thr_work", configuration=_configuration(), initial_state=saved.ref
        )
        assert await store.repair_read_models() == (before.thread_id,)
        assert (await store.threads.get(before.thread_id)) == before
        assert await store.work.read("thr_broken", _initial()) is None
        assert await store.work.missing() == (("thr_broken", _initial()),)
    async with open_local_store(settings) as restarted:
        projection = await restarted.work.read("thr_work", saved.ref, ("tasks", "notes"))
        summary = WorkSummary.model_validate_json(projection.summary_json)
        assert summary.tasks.total == 300 and summary.tasks.completed == 299
        assert summary.tasks.active.task_id == "task-300"  # Outside the bounded first page.
        assert summary.notes.total == 100
        task_page = TaskPage.model_validate_json(projection.tasks_json)
        note_page = NotePage.model_validate_json(projection.notes_json)
        assert len(task_page.tasks) == 256 and task_page.omitted == 44
        assert len(note_page.notes) < 100 and note_page.total == 100
        assert sum(len(note.key.encode()) + len(note.value.encode()) for note in note_page.notes) <= 256 * 1024
        async with short_session(restarted.database.sessions) as session:
            row = await session.get(ThreadWorkRecord, "thr_work")
            assert len(row.summary_json) < 2048


def test_saved_work_does_not_persist_provider_observations() -> None:
    working = WorkingState(
        task_mode="provider",
        provider_cursor=ProviderTaskCursor(provider_type="test", state_version="1"),
        notes={"decision": "retained"},
    )
    data = build_work_projection(state("thr_provider", working))
    summary = WorkSummary.model_validate_json(data.summary_json)
    assert summary.notes.available and summary.notes.total == 1
    assert not summary.tasks.available and summary.tasks.source == "unavailable"
    assert TaskPage.model_validate_json(data.tasks_json).available is False


async def test_work_repair_rechecks_head_after_object_loading(tmp_path: Path, monkeypatch) -> None:
    from a13n_harness_ui.storage import StoredContinuation

    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        initial = await store.objects.publish_model(
            object_kind=ObjectKind.thread_initial_state,
            value=StoredThreadInitialState(
                harness_state=state("thr_work", WorkingState(notes={"value": "old"})), created_at=datetime.now(UTC)
            ),
        )
        await store.threads.create(thread_id="thr_work", configuration=_configuration(), initial_state=initial.ref)
        saved = await store.objects.publish_model(
            object_kind=ObjectKind.continuation,
            value=StoredContinuation(
                harness_release="test",
                created_at=datetime.now(UTC),
                run_composition=ObjectRef(
                    object_kind=ObjectKind.run_composition, object_schema_version="1", logical_digest="c" * 64
                ),
                harness_state=state("thr_work", WorkingState(notes={"value": "new"})),
                display=DisplaySnapshot(
                    position=DisplayPosition(producer=Producer(run_id="run-work", generation="test")),
                    scopes=(DisplayScope(id="run-work", thread_id="thr_work", run_id="run-work"),),
                ),
            ),
        )
        read = store.objects.read_model

        async def advance(ref, model):
            value = await read(ref, model)
            await store.threads.select_continuation(
                thread_id="thr_work", expected=None, replacement=saved.ref, read_model=ThreadReadModel()
            )
            return value

        with monkeypatch.context() as patch:
            patch.setattr(store.objects, "read_model", advance)
            assert await store.repair_read_models() == ()
        assert await store.work.read("thr_work", saved.ref) is None
        assert await store.repair_read_models() == ("thr_work",)
        data = await store.work.read("thr_work", saved.ref, ("notes",))
        assert NotePage.model_validate_json(data.notes_json).notes[0].value == "new"


def test_work_migration_upgrade_and_downgrade_preserve_selected_heads(tmp_path: Path) -> None:
    from a13n_harness_ui.storage.migration import DatabaseMigrator
    from alembic import command
    from sqlalchemy import create_engine, inspect, text

    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "1da116a90fda"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    values = {"now": datetime.now(UTC).isoformat(), "initial": "a" * 64, "continuation": "b" * 64}
    try:
        with engine.begin() as connection:
            connection.execute(
                text("""
                INSERT INTO thread (thread_id, metadata_version, archived, created_at, updated_at,
                                    initial_state_schema_version, initial_state_digest,
                                    continuation_schema_version, continuation_digest)
                VALUES ('thr_work', 1, 0, :now, :now, '1', :initial, '1', :continuation)
            """),
                values,
            )
        migrator.upgrade()
        with engine.begin() as connection:
            assert connection.execute(text("SELECT count(*) FROM thread_work")).scalar() == 0
            connection.execute(
                text("""
                INSERT INTO thread_work (thread_id, source_id, object_schema_version, version, summary_json)
                VALUES ('thr_work', :continuation, '1', 1, '{}')
            """),
                values,
            )
        migrator._run(lambda config: command.downgrade(config, "1da116a90fda"), write=True)
        assert "thread_work" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT initial_state_digest, continuation_digest FROM thread")).one() == (
                values["initial"],
                values["continuation"],
            )
        migrator.upgrade()
        migrator.verify_current()
    finally:
        engine.dispose()


async def test_failed_work_publication_preserves_previous_projection(tmp_path: Path) -> None:
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        thread = await store.threads.create(
            thread_id="thr_work", configuration=_configuration(), initial_state=_initial()
        )
        original = WorkData("old", "old tasks", "old notes")
        assert await store.work.publish(thread.thread_id, thread.initial_state, original)

        def fail(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO thread_work"):
                raise RuntimeError("injected publication failure")

        event.listen(store.database.engine.sync_engine, "before_cursor_execute", fail)
        try:
            assert not await store.publish_work(
                thread.thread_id, thread.initial_state, HarnessState.new(thread_id=thread.thread_id)
            )
        finally:
            event.remove(store.database.engine.sync_engine, "before_cursor_execute", fail)
        assert await store.work.read(thread.thread_id, thread.initial_state, ("tasks", "notes")) == original
        assert (await store.threads.get(thread.thread_id)) == thread
