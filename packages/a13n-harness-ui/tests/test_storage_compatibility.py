"""Compatible additive migrations do not evict active or reconnecting older Apps."""

from __future__ import annotations

import shutil
from contextlib import AsyncExitStack
from datetime import UTC, datetime
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
from anyio import Event, fail_after
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import MetaData, create_engine, inspect, text

from .test_app import _write_configuration

pytestmark = pytest.mark.anyio


def older_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, MetaData]:
    """Exercise the real comment upgrades with a current-code reader stand-in.

    Keep unrelated navigation columns on both sides of this historical fixture;
    the navigation migration itself has separate real old-schema coverage.
    """
    newer = tmp_path / "comment-migrations"
    shutil.copytree(migration.MIGRATIONS_PATH, newer, ignore=shutil.ignore_patterns("__pycache__"))
    (newer / "versions/20261010_3cf95de9550a_retire_saved_output_comments.py").unlink()
    (newer / "versions/20261010_4bd3d3ab1b82_retain_latest_root_execution_summary.py").unlink()
    (newer / "versions/20261001_c82e5ae6ef80_add_small_thread_work_projections.py").unlink()
    (newer / "versions/20260926_1da116a90fda_add_memory_scope_to_observable_threads.py").unlink()
    (newer / "versions/20260924_6fb2512c92a3_add_shared_thread_stars.py").unlink()
    (newer / "versions/20260924_0a7582171995_replace_project_leads_with_thread_.py").unlink()
    (newer / "versions/20260923_78e4e7206898_add_project_lead_worker_ownership.py").unlink()
    (newer / "versions/20260923_027c7c879425_add_canonical_project_lead.py").unlink()
    (newer / "versions/20260918_ba240ec65035_add_planned_update_handoff.py").unlink()
    (newer / "versions/20260918_63e8be47c2e2_replace_push_thread_interest_with_.py").unlink()
    (newer / "versions/20260919_3a52b4914bbb_add_thread_owned_local_roots.py").unlink()
    (newer / "versions/20260919_0c38589db1f4_add_device_environment_bindings_and_.py").unlink()
    (newer / "versions/20260918_e416fbd4674c_add_thread_default_model.py").unlink()
    (newer / "versions/20260917_768a6a993a59_add_indexed_thread_inspection_.py").unlink()
    (newer / "versions/20260917_acd7efeb9fd8_add_continuation_read_models.py").unlink()
    (newer / "versions/20260917_9aeed42d15b3_add_browser_push_subscriptions.py").unlink()
    (newer / "versions/20260916_57b54299e47e_add_durable_thread_completion_markers.py").unlink()
    (newer / "versions/20260916_122039abf689_add_thread_navigation_touch_time.py").unlink()
    older = tmp_path / "older-migrations"
    shutil.copytree(newer, older)
    monkeypatch.setattr(migration, "MIGRATIONS_PATH", newer)
    (older / "versions/20260912_4b71199c8ee5_add_saved_output_comments.py").unlink()
    (older / "versions/20260915_20e4b84abfd1_add_comment_editing_and_deletion.py").unlink()
    metadata = MetaData()
    for table in harness_ui_metadata().sorted_tables:
        if table.name not in {"output_comment", "output_comment_tombstone"}:
            table.to_metadata(metadata)
    path = tmp_path / "data" / "metadata.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    with monkeypatch.context() as patch:
        patch.setattr(migration, "MIGRATIONS_PATH", older)
        DatabaseMigrator(path)._run(lambda config: command.upgrade(config, "head"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE thread_configuration ADD COLUMN default_model_id VARCHAR(128)"))
            connection.execute(
                text("ALTER TABLE thread_configuration ADD COLUMN local_roots_json JSON NOT NULL DEFAULT '[]'")
            )
            connection.execute(
                text("ALTER TABLE thread_configuration ADD COLUMN environment_bindings_json TEXT NOT NULL DEFAULT '[]'")
            )
            connection.execute(text("ALTER TABLE thread_configuration ADD COLUMN default_environment VARCHAR(63)"))
            connection.execute(
                text("ALTER TABLE environment_binding ADD COLUMN device_id VARCHAR(128) NOT NULL DEFAULT ''")
            )
            connection.execute(text("ALTER TABLE environment_binding ADD COLUMN alias VARCHAR(63) NOT NULL DEFAULT ''"))
            connection.execute(text("ALTER TABLE thread ADD COLUMN completion_version INTEGER NOT NULL DEFAULT 0"))
            connection.execute(text("ALTER TABLE thread ADD COLUMN completion_run_id VARCHAR(80)"))
            connection.execute(text("ALTER TABLE thread ADD COLUMN completion_digest VARCHAR(64)"))
            connection.execute(text("ALTER TABLE thread ADD COLUMN completed_at DATETIME"))
            connection.execute(text("ALTER TABLE thread ADD COLUMN touched_at DATETIME"))
            connection.execute(text("ALTER TABLE thread ADD COLUMN starred BOOLEAN NOT NULL DEFAULT 0"))
            connection.execute(text("ALTER TABLE thread ADD COLUMN memory_scope VARCHAR(256)"))
            connection.execute(text("CREATE UNIQUE INDEX uq_thread_memory_scope ON thread (memory_scope)"))
            connection.execute(text("ALTER TABLE thread ADD COLUMN read_model_digest VARCHAR(64)"))
            connection.execute(text("ALTER TABLE thread ADD COLUMN read_model_schema_version VARCHAR(64)"))
            connection.execute(text("ALTER TABLE thread ADD COLUMN read_model_json TEXT"))
            connection.execute(text("ALTER TABLE thread ADD COLUMN last_execution_json TEXT"))
            connection.execute(text("CREATE INDEX ix_thread_touched_at ON thread (touched_at)"))
            # Keep unrelated query/push tables on both sides of this comment-only fixture.
            for name in (
                "coordinator",
                "coordinator_worker",
                "planned_restart",
                "web_push_key",
                "web_push_subscription",
                "thread_inspection",
                "thread_work",
                "transcript_entry",
                "transcript_turn",
            ):
                harness_ui_metadata().tables[name].create(connection)
    finally:
        engine.dispose()
    return older, metadata


async def test_old_app_saves_run_across_new_app_migration_and_reconnects(tmp_path, monkeypatch):
    configuration = _write_configuration(tmp_path)
    older, metadata = older_package(tmp_path, monkeypatch)
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
            ).scalar_one() == "20e4b84abfd1"
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


def test_comment_retirement_preserves_threads_and_requires_backup_for_rollback(tmp_path, monkeypatch):
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "4bd3d3ab1b82"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                harness_ui_metadata()
                .tables["thread"]
                .insert()
                .values(
                    thread_id="thread-old",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    initial_state_schema_version="1",
                    initial_state_digest="a" * 64,
                    continuation_schema_version="1",
                    continuation_digest="b" * 64,
                    completion_digest="c" * 64,
                    completion_run_id="run-old",
                    completion_version=1,
                )
            )
            connection.execute(
                text(
                    "INSERT INTO output_comment_tombstone VALUES ('comment-deleted', 'thread-old', '2026-10-10 00:00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO output_comment (comment_id, root_thread_id, producing_thread_id, target_key, "
                    "source_kind, source_schema_version, source_digest, publication_json, created_at) "
                    "VALUES ('comment-old', 'thread-old', 'thread-old', :digest, 'continuation', '1', :digest, '{}', '2026-10-10 00:00:00')"
                ),
                {"digest": "b" * 64},
            )
            before = connection.execute(text("SELECT * FROM thread")).all()
            old_metadata = MetaData()
            old_metadata.reflect(connection)
        migrator.upgrade()
        migrator.upgrade()
        migrator.verify_current()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT * FROM thread")).all() == before
            assert "output_comment" not in inspect(connection).get_table_names()
            assert "output_comment_tombstone" not in inspect(connection).get_table_names()
        with monkeypatch.context() as patch:
            patch.setattr(migration, "harness_ui_metadata", lambda: old_metadata)
            with pytest.raises(DatabaseSchemaError, match="missing required table output_comment"):
                migrator.verify_current()
        with pytest.raises(RuntimeError, match="restore a pre-upgrade backup"):
            migrator._run(lambda config: command.downgrade(config, "4bd3d3ab1b82"), write=True)
    finally:
        engine.dispose()


async def test_memory_migration_preserves_existing_threads_and_enforces_scope_identity(tmp_path):
    from sqlalchemy.exc import IntegrityError

    configuration = _write_configuration(tmp_path)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    migrator = DatabaseMigrator(settings.storage.data_root / "metadata.sqlite3")
    migrator._run(lambda config: command.upgrade(config, "6fb2512c92a3"), write=True)
    engine = create_engine(f"sqlite:///{settings.storage.data_root / 'metadata.sqlite3'}")
    try:
        assert "memory_scope" not in {column["name"] for column in inspect(engine).get_columns("thread")}
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO thread (thread_id, title, metadata_version, archived, created_at, updated_at, "
                    "initial_state_schema_version, initial_state_digest) "
                    "VALUES ('thread-ordinary', 'Before Memory', 1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, '1', :digest)"
                ),
                {"digest": "a" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO thread_configuration (thread_id, version, project_id, agent_source_kind, "
                    "agent_source_id, environment_profile_id, harness_plugin_ids_json, "
                    "environment_run_extension_ids_json, mcp_server_ids_json) "
                    "VALUES ('thread-ordinary', 1, 'project-main', 'agent', 'agent-assistant', 'environment-native', '[]', '[]', '[]')"
                )
            )
            before = connection.execute(text("SELECT * FROM thread")).mappings().one()
            before_config = connection.execute(text("SELECT * FROM thread_configuration")).mappings().one()
        migrator.upgrade()
        with engine.connect() as connection:
            after = connection.execute(text("SELECT * FROM thread")).mappings().one()
            assert dict(after) == {**before, "memory_scope": None, "last_execution_json": None}
            assert connection.execute(text("SELECT * FROM thread_configuration")).mappings().one() == before_config
        async with open_harness_ui_app(settings, configuration_path=configuration) as app:
            memory = await app._threads.memory_thread(scope="global", project_id=None, model_id="model-primary")
            assert (
                await app._threads.memory_thread(scope="global", project_id=None, model_id="model-primary")
            ).thread_id == memory.thread_id
        with engine.begin() as connection, pytest.raises(IntegrityError):
            connection.execute(
                text("UPDATE thread SET memory_scope = 'global' WHERE thread_id = :id"), {"id": "thread-ordinary"}
            )
        migrator.upgrade()
        migrator.verify_current()
    finally:
        engine.dispose()
