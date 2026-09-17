"""Continuation-bound query data stays cheap without becoming execution authority."""

from datetime import UTC, datetime

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.composition import AgentReconstructor
from a13n_harness_ui.errors import StoreConflictError
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.storage import ObjectKind, ObjectRef, open_local_store
from a13n_harness_ui.storage.contracts import ThreadReadModel
from a13n_harness_ui.storage.database import transaction
from a13n_harness_ui.storage.models import ThreadRecord
from a13n_harness_ui.surfaces import RootOperationStatus
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import DeferredToolRequests
from sqlalchemy import update

from .test_app import _reconstructed, _write_configuration
from .test_thread_repository import _configuration, _initial

pytestmark = pytest.mark.anyio


def reference(character):
    return ObjectRef(object_kind=ObjectKind.continuation, object_schema_version="1", logical_digest=character * 64)


async def test_projection_selection_conflict_and_stale_projection_repair(tmp_path):
    first, second = reference("a"), reference("b")
    projection = ThreadReadModel(deferred_requests=DeferredToolRequests(calls=[ToolCallPart("ask_user_question", {})]))
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        await store.threads.create(thread_id="thread-one", configuration=_configuration(), initial_state=_initial())
        selected = await store.threads.select_continuation(
            thread_id="thread-one", expected=None, replacement=first, read_model=projection
        )
        assert selected.read_model == projection
        with pytest.raises(StoreConflictError):
            await store.threads.select_continuation(
                thread_id="thread-one", expected=None, replacement=second, read_model=ThreadReadModel()
            )
        assert (await store.threads.get("thread-one")).read_model == projection
        # Stale derived data cannot be used for a different continuation.
        async with transaction(store.database.sessions) as session:
            await session.execute(
                update(ThreadRecord)
                .where(ThreadRecord.thread_id == "thread-one")
                .values(continuation_digest=second.logical_digest)
            )
        assert (await store.threads.get("thread-one")).read_model is None
        assert await store.threads.read_models(("thread-one",)) == {}
        assert await store.threads.missing_read_models() == (("thread-one", second),)
        assert not await store.threads.repair_read_model("thread-one", first, projection)
        assert await store.threads.repair_read_model("thread-one", second, ThreadReadModel())
        repaired = await store.threads.get("thread-one")
        assert repaired.read_model == ThreadReadModel()
        assert repaired.updated_at == selected.updated_at


async def test_activity_detail_and_status_never_decode_continuations(tmp_path, monkeypatch):
    configuration = _write_configuration(tmp_path)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)

    async def stream(messages, info):
        yield "A retained answer."

    def reconstruct(self, composition, *, root_capabilities=(), **kwargs):
        return _reconstructed(stream, root_capabilities)

    monkeypatch.setattr(AgentReconstructor, "reconstruct", reconstruct)
    async with open_harness_ui_app(settings, configuration_path=configuration) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Complete")
        assert (
            await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        ).status is RootOperationStatus.completed
        stored = await app._store.threads.get(thread.thread_id)
        assert stored.read_model.latest_activity.text == "A retained answer."
        read = app._store.objects.read_model

        async def guarded(ref, model):
            assert ref.object_kind is not ObjectKind.continuation
            return await read(ref, model)

        monkeypatch.setattr(app._store.objects, "read_model", guarded)
        detail = await app.get_thread(thread.thread_id)
        assert "run" in detail.available_actions
        page = await app.thread_activity(project_id=None, limit=5)
        assert page.rows[0].thread.thread_id == thread.thread_id
        await app.status()
        # A missing/stale projection is not evidence that a suspended decision is absent.
        async with transaction(app._store.database.sessions) as session:
            await session.execute(
                update(ThreadRecord).where(ThreadRecord.thread_id == thread.thread_id).values(read_model_digest=None)
            )
        detail = await app.get_thread(thread.thread_id)
        assert "run" not in detail.available_actions and "respond" not in detail.available_actions
        monkeypatch.setattr(app._store.objects, "read_model", read)
        before = datetime.now(UTC)
        assert await app._store.repair_read_models() == (thread.thread_id,)
        assert (await app._store.threads.get(thread.thread_id)).updated_at < before
        assert "run" in (await app.get_thread(thread.thread_id)).available_actions


async def test_verified_configuration_cache_returns_detached_values(tmp_path, monkeypatch):
    configuration = _write_configuration(tmp_path)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=configuration) as app:
        source = await app._configurations.current()
        expected = set(source.agents)
        source.agents.clear()
        reads = []
        original = app._store.objects.read_model

        async def counted(ref, model):
            reads.append(ref)
            return await original(ref, model)

        monkeypatch.setattr(app._store.objects, "read_model", counted)
        assert set((await app._configurations.current()).agents) == expected
        assert not reads


async def test_read_model_migration_keeps_older_writers_and_selected_heads(tmp_path):
    from a13n_harness_ui.storage.migration import DatabaseMigrator
    from alembic import command
    from sqlalchemy import create_engine, text

    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "9aeed42d15b3"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    insert = text("""
        INSERT INTO thread (thread_id, metadata_version, archived, created_at, updated_at,
                            initial_state_schema_version, initial_state_digest, continuation_schema_version,
                            continuation_digest)
        VALUES (:id, 1, 0, :now, :now, '1', :initial, '1', :continuation)
    """)
    values = {
        "id": "thread-before",
        "now": datetime.now(UTC).isoformat(),
        "initial": "1" * 64,
        "continuation": "a" * 64,
    }
    try:
        with engine.begin() as connection:
            connection.execute(insert, values)
        migrator.upgrade()
        with engine.begin() as connection:
            connection.execute(insert, {**values, "id": "thread-after"})
            rows = connection.execute(text("SELECT continuation_digest, read_model_json FROM thread")).all()
            assert rows == [("a" * 64, None), ("a" * 64, None)]
        migrator._run(lambda config: command.downgrade(config, "9aeed42d15b3"), write=True)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM thread")).scalar() == 2
    finally:
        engine.dispose()


async def test_cached_history_projections_detach_nested_metadata(tmp_path, monkeypatch):
    from pydantic_ai.messages import TextContent

    configuration = _write_configuration(tmp_path)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)

    async def stream(messages, info):
        yield "Retained answer."

    monkeypatch.setattr(
        AgentReconstructor,
        "reconstruct",
        lambda self, composition, *, root_capabilities=(), **kwargs: _reconstructed(stream, root_capabilities),
    )
    async with open_harness_ui_app(settings, configuration_path=configuration) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(
            thread_id=thread.thread_id,
            prompt=[TextContent("Question", metadata={"harness_ui": {"composer": {"index": 0}}})],
        )
        await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        first = await app.get_thread_transcript(thread_id=thread.thread_id)
        part = next(part for entry in first.entries for part in entry.parts if part.text == "Question")
        part.metadata.model_extra["harness_ui"]["composer"]["index"] = 999
        original = app._store.objects.read_model

        async def guarded(ref, model):
            assert ref.object_kind is not ObjectKind.continuation
            return await original(ref, model)

        monkeypatch.setattr(app._store.objects, "read_model", guarded)
        second = await app.get_thread_transcript(thread_id=thread.thread_id)
        part = next(part for entry in second.entries for part in entry.parts if part.text == "Question")
        assert part.metadata.model_extra["harness_ui"]["composer"]["index"] == 0


async def test_repair_does_not_log_exception_payloads(tmp_path, monkeypatch):
    from a13n_harness_ui.storage import runtime

    class Logger:
        def warning(self, message, *, extra):
            messages.append((message, extra))

    messages = []
    monkeypatch.setattr(runtime, "get_logger", lambda _: Logger())
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        await store.threads.create(thread_id="thread-one", configuration=_configuration(), initial_state=_initial())
        await store.threads.select_continuation(
            thread_id="thread-one", expected=None, replacement=reference("a"), read_model=ThreadReadModel()
        )
        async with transaction(store.database.sessions) as session:
            await session.execute(
                update(ThreadRecord).where(ThreadRecord.thread_id == "thread-one").values(read_model_digest=None)
            )

        async def invalid(ref, model):
            raise ValueError("PRIVATE_PAYLOAD_MARKER")

        monkeypatch.setattr(store.objects, "read_model", invalid)
        assert await store.repair_read_models() == ()
        assert messages == [
            ("Could not rebuild Thread read model", {"thread_id": "thread-one", "error_type": "ValueError"})
        ]
