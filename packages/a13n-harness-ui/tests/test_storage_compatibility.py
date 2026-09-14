"""Compatible additive migrations do not evict active or reconnecting older Apps."""

from __future__ import annotations

import shutil
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.storage import migration
from a13n_harness_ui.storage.database import check_database
from a13n_harness_ui.storage.metadata import harness_ui_metadata
from a13n_harness_ui.storage.migration import DatabaseMigrator, DatabaseSchemaError
from a13n_harness_ui.surfaces import RootOperationStatus
from alembic import command
from anyio import Event, fail_after, to_thread
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import MetaData, create_engine, inspect, text

from .test_app import _write_configuration

pytestmark = pytest.mark.anyio


def older_package(tmp_path: Path) -> tuple[Path, MetaData]:
    """Use the real previous schema/history, with the forward-compatible reader."""
    older = tmp_path / "older-migrations"
    shutil.copytree(migration.MIGRATIONS_PATH, older, ignore=shutil.ignore_patterns("__pycache__"))
    (older / "versions/20260912_4b71199c8ee5_add_saved_output_comments.py").unlink()
    metadata = MetaData()
    for table in harness_ui_metadata().sorted_tables:
        if table.name != "output_comment":
            table.to_metadata(metadata)
    return older, metadata


async def test_old_app_saves_run_across_new_app_migration_and_reconnects(tmp_path, monkeypatch):
    configuration = _write_configuration(tmp_path)
    older, metadata = older_package(tmp_path)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    started, release = Event(), Event()

    async def stream(messages, info):
        started.set()
        await release.wait()
        yield "Saved across a compatible database upgrade."

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with AsyncExitStack() as stack:
        with monkeypatch.context() as patch:
            patch.setattr(migration, "MIGRATIONS_PATH", older)
            patch.setattr(migration, "harness_ui_metadata", lambda: metadata)
            old = await stack.enter_async_context(open_harness_ui_app(settings, configuration_path=configuration))
            thread = await old.create_thread()
            receipt = await old.submit_thread(thread_id=thread.thread_id, prompt="Wait through migration")
            with fail_after(10):
                await started.wait()
        # This App applies the actual generated comment migration while the first
        # App still has a live Run and its original pooled SQLite connections.
        new = await stack.enter_async_context(open_harness_ui_app(settings, configuration_path=configuration))
        new_thread = await new.create_thread(title="New writer")
        release.set()
        result = await old.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        assert result.status is RootOperationStatus.completed
        saved = await new.get_thread_transcript(thread_id=thread.thread_id)
        assert "Saved across" in saved.model_dump_json()
        assert (await old.get_thread(new_thread.thread_id)).thread.title == "New writer"
        with monkeypatch.context() as patch:
            patch.setattr(migration, "MIGRATIONS_PATH", older)
            patch.setattr(migration, "harness_ui_metadata", lambda: metadata)
            await check_database(old._store.database)
            reopened = await stack.enter_async_context(open_harness_ui_app(settings, configuration_path=configuration))
            created = await reopened.create_thread(title="Older reconnect")
            assert (await new.get_thread(created.thread_id)).thread.title == "Older reconnect"
            assert await reopened.get_thread_transcript(thread_id=thread.thread_id) == saved
        async with new._store.database.engine.connect() as connection:
            assert (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one() == "4b71199c8ee5"
            assert (await connection.execute(text("SELECT count(*) FROM output_comment"))).scalar_one() == 0


def test_future_revision_with_required_schema_is_left_unchanged(tmp_path):
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator.upgrade()
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE future_feature (id TEXT PRIMARY KEY)"))
            connection.execute(text("ALTER TABLE thread ADD COLUMN future_label TEXT"))
            connection.execute(text("UPDATE alembic_version SET version_num = 'future-compatible'"))
        migrator.upgrade()
        migrator.verify_current()
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "future-compatible"
            )
        assert "future_feature" in inspect(engine).get_table_names()
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE thread RENAME COLUMN title TO removed_title"))
        with pytest.raises(DatabaseSchemaError, match="missing required columns: title"):
            migrator.upgrade()
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "future-compatible"
            )
    finally:
        engine.dispose()


async def test_comment_migration_preserves_heads_and_refuses_nonempty_downgrade(tmp_path, monkeypatch):
    from a13n_harness_ui.output_comment_models import CommentPublication

    from .test_comment_protocol import publication

    configuration = _write_configuration(tmp_path)
    older, metadata = older_package(tmp_path)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)

    async def stream(messages, info):
        yield "Comment migration source"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    with monkeypatch.context() as patch:
        patch.setattr(migration, "MIGRATIONS_PATH", older)
        patch.setattr(migration, "harness_ui_metadata", lambda: metadata)
        async with open_harness_ui_app(settings, configuration_path=configuration) as old:
            thread = await old.create_thread()
            receipt = await old.submit_thread(thread_id=thread.thread_id, prompt="Save")
            assert (await old.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
            before = await old.get_thread_transcript(thread_id=thread.thread_id)
    path = settings.storage.data_root / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator.upgrade()
    # Empty downgrade removes only the additive table; re-upgrade is repeatable.
    migrator._run(lambda config: command.downgrade(config, "9c59aaa12f44"), write=True)
    migrator.upgrade()
    async with open_harness_ui_app(settings, configuration_path=configuration) as app:
        assert await app.get_thread_transcript(thread_id=thread.thread_id) == before
        assert (await app.list_output_comments(thread.thread_id)).comments == ()
        target = next(
            part.comment_target for entry in before.entries for part in entry.parts if part.comment_target is not None
        )
        comment = await app.publish_output_comment(
            thread.thread_id, CommentPublication.model_validate(publication(target.model_dump()))
        )
        with pytest.raises(RuntimeError, match="Cannot downgrade while output comments exist"):
            await to_thread.run_sync(
                lambda: migrator._run(lambda config: command.downgrade(config, "9c59aaa12f44"), write=True)
            )
        assert await app.get_output_comment(thread.thread_id, comment.comment_id) == comment
        assert await app.get_thread_transcript(thread_id=thread.thread_id) == before
        migrator.verify_current()
