from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage.database import open_database, short_session, transaction
from a13n_harness_ui.storage.metadata import harness_ui_metadata
from a13n_harness_ui.storage.migration import DatabaseMigrator, DatabaseSchemaError
from a13n_harness_ui.storage.models import AcceptedConfigurationRecord
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import DateTime, bindparam, create_engine, inspect, select, text

pytestmark = pytest.mark.anyio


def test_migration_history_clean_upgrade_and_schema_parity(tmp_path: Path) -> None:
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)

    migrator.verify_history()
    migrator.upgrade()
    migrator.verify_current()

    engine = create_engine(f"sqlite:///{path}")
    try:
        assert set(inspect(engine).get_table_names()) == {
            "accepted_configuration",
            "alembic_version",
            "child_execution",
            "configuration_source",
            "current_configuration",
            "environment_binding",
            "project_model_preference",
            "output_comment",
            "resource_index",
            "thread",
            "thread_configuration",
            "thread_usage",
        }
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "compare_server_default": True, "render_as_batch": True},
            )
            assert compare_metadata(context, harness_ui_metadata()) == []
    finally:
        engine.dispose()


def test_populated_session_store_upgrade_fails_before_schema_change(tmp_path: Path) -> None:
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(  # pyright: ignore[reportPrivateUsage]
        lambda config: command.upgrade(config, "41ec8abea31a"),
        write=True,
    )
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO accepted_configuration "
                    "(source_digest, yaml_digest, document_json, accepted_at) "
                    "VALUES (:source, :yaml, :document, :accepted_at)"
                ).bindparams(bindparam("accepted_at", type_=DateTime())),
                {
                    "source": "1" * 64,
                    "yaml": "2" * 64,
                    "document": "{}",
                    "accepted_at": datetime.now(UTC).replace(tzinfo=None),
                },
            )

        with pytest.raises(RuntimeError, match="cannot be safely converted"):
            migrator.upgrade()

        assert "local_session" in inspect(engine).get_table_names()
        assert "thread" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT source_digest FROM accepted_configuration")).scalar_one() == "1" * 64
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "41ec8abea31a"
    finally:
        engine.dispose()


def test_thread_metadata_migration_backfills_existing_rows(tmp_path: Path) -> None:
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(  # pyright: ignore[reportPrivateUsage]
        lambda config: command.upgrade(config, "f293cefc6ea1"),
        write=True,
    )
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO thread "
                    "(thread_id, parent_thread_id, title, archived, created_at, updated_at, "
                    "initial_state_schema_version, initial_state_digest, continuation_schema_version, "
                    "continuation_digest) VALUES "
                    "(:thread_id, NULL, :title, 0, :created_at, :updated_at, '1', :digest, NULL, NULL)"
                ).bindparams(
                    bindparam("created_at", type_=DateTime()),
                    bindparam("updated_at", type_=DateTime()),
                ),
                {
                    "thread_id": "thread-existing",
                    "title": "Existing",
                    "created_at": datetime.now(UTC).replace(tzinfo=None),
                    "updated_at": datetime.now(UTC).replace(tzinfo=None),
                    "digest": "1" * 64,
                },
            )
        migrator.upgrade()
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT metadata_version FROM thread WHERE thread_id = 'thread-existing'")
                ).scalar_one()
                == 1
            )
            row = connection.execute(
                text(
                    "SELECT search_text, first_input, latest_reply, activity_at, initial_state_digest FROM thread WHERE thread_id = 'thread-existing'"
                )
            ).one()
            assert row.search_text == "thread-existing\nexisting"
            assert row.first_input == row.latest_reply == ""
            assert row.activity_at is None
            assert row.initial_state_digest == "1" * 64
        migrator.upgrade()
        migrator.verify_current()
    finally:
        engine.dispose()


def test_thread_store_downgrade_is_rejected_before_schema_change(tmp_path: Path) -> None:
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator.upgrade()

    with pytest.raises(RuntimeError, match="cannot be safely downgraded"):
        migrator._run(  # pyright: ignore[reportPrivateUsage]
            lambda config: command.downgrade(config, "41ec8abea31a"),
            write=True,
        )

    migrator.verify_current()
    engine = create_engine(f"sqlite:///{path}")
    try:
        assert "thread" in inspect(engine).get_table_names()
        assert "local_session" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_migration_verification_rejects_an_unknown_database_revision(tmp_path: Path) -> None:
    path = tmp_path / "metadata.sqlite3"
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            connection.execute(text("INSERT INTO alembic_version (version_num) VALUES ('unknown')"))
    finally:
        engine.dispose()

    with pytest.raises(DatabaseSchemaError, match="missing required table"):
        DatabaseMigrator(path).verify_current()


async def test_database_configures_sqlite_and_short_transactions(tmp_path: Path) -> None:
    settings = StorageSettings(data_root=tmp_path, busy_timeout_seconds=1.25)
    path = tmp_path / "metadata.sqlite3"

    async with open_database(path, settings) as database:
        async with database.engine.connect() as connection:
            assert (await connection.execute(text("PRAGMA journal_mode"))).scalar_one() == "wal"
            assert (await connection.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
            assert (await connection.execute(text("PRAGMA busy_timeout"))).scalar_one() == 1250
            assert (await connection.execute(text("PRAGMA synchronous"))).scalar_one() == 2

        now = datetime.now(UTC)
        async with transaction(database.sessions) as session:
            session.add(
                AcceptedConfigurationRecord(
                    generation_digest="1" * 64,
                    object_schema_version="1",
                    object_digest="2" * 64,
                    accepted_at=now,
                )
            )

        with pytest.raises(RuntimeError, match="rollback"):
            async with transaction(database.sessions) as session:
                session.add(
                    AcceptedConfigurationRecord(
                        generation_digest="3" * 64,
                        object_schema_version="1",
                        object_digest="4" * 64,
                        accepted_at=now,
                    )
                )
                raise RuntimeError("rollback")

        async with short_session(database.sessions) as session:
            records = tuple((await session.execute(select(AcceptedConfigurationRecord))).scalars())
            assert [record.generation_digest for record in records] == ["1" * 64]
            assert records[0].accepted_at.tzinfo is UTC


def test_mcp_bundle_index_upgrade_preserves_rows_and_rejects_lossy_downgrade(tmp_path: Path) -> None:
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "11422c5bac45"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO accepted_configuration (generation_digest, object_schema_version, object_digest, accepted_at) "
                    "VALUES (:digest, '1', :digest, :created)"
                ),
                {"digest": "a" * 64, "created": datetime.now(UTC).isoformat()},
            )
            connection.execute(
                text(
                    "INSERT INTO resource_index "
                    "(generation_digest, relative_path, resource_kind, resource_id, name, source_digest, normalized_digest) "
                    "VALUES (:digest, 'mcp/servers.json', 'mcp_server', 'mcp-one', 'One', :digest, :digest)"
                ),
                {"digest": "a" * 64},
            )
        migrator.upgrade()
        migrator.upgrade()
        migrator.verify_current()
        assert inspect(engine).get_pk_constraint("resource_index")["constrained_columns"] == [
            "generation_digest",
            "resource_kind",
            "resource_id",
        ]
        with engine.begin() as connection:
            assert connection.execute(text("SELECT resource_id FROM resource_index")).scalar_one() == "mcp-one"
            connection.execute(
                text(
                    "INSERT INTO resource_index "
                    "(generation_digest, relative_path, resource_kind, resource_id, name, source_digest, normalized_digest) "
                    "VALUES (:digest, 'mcp/servers.json', 'mcp_server', 'mcp-two', 'Two', :digest, :digest)"
                ),
                {"digest": "a" * 64},
            )
        with pytest.raises(RuntimeError, match="multi-server"):
            migrator._run(lambda config: command.downgrade(config, "11422c5bac45"), write=True)
        migrator.verify_current()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM resource_index")).scalar_one() == 2
    finally:
        engine.dispose()


async def test_project_model_preferences_are_independent_and_last_write_wins(tmp_path: Path) -> None:
    import asyncio

    from a13n_harness_ui.storage.repositories import ProjectModelPreferenceRepository

    settings = StorageSettings(data_root=tmp_path)
    path = tmp_path / "metadata.sqlite3"
    async with open_database(path, settings) as first_db, open_database(path, settings) as second_db:
        first = ProjectModelPreferenceRepository(first_db.sessions)
        second = ProjectModelPreferenceRepository(second_db.sessions)
        await asyncio.gather(first.set("project-a", "model-a"), second.set("project-b", "model-b"))
        assert await first.get("project-b") == "model-b"
        assert await second.get("project-a") == "model-a"
        await second.set("project-a", "model-new")
        assert await first.get("project-a") == "model-new"
        await first.set("project-a", None)
        await first.set("project-a", None)
        assert await second.get("project-a") is None
        assert await second.get("project-b") == "model-b"
    async with open_database(path, settings) as database:
        repository = ProjectModelPreferenceRepository(database.sessions)
        assert await repository.get("project-a") is None
        assert await repository.get("project-b") == "model-b"


def test_project_model_preference_migration_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(  # pyright: ignore[reportPrivateUsage]
        lambda config: command.upgrade(config, "a65ad8a5330d"), write=True
    )
    migrator.upgrade()
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO project_model_preference VALUES ('project-a', 'model-a')"))
        migrator.upgrade()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT model_id FROM project_model_preference")).scalar_one() == "model-a"
        migrator._run(  # pyright: ignore[reportPrivateUsage]
            lambda config: command.downgrade(config, "a65ad8a5330d"), write=True
        )
        assert "project_model_preference" not in inspect(engine).get_table_names()
        assert "thread_configuration" in inspect(engine).get_table_names()
        migrator.upgrade()
        migrator.verify_current()
    finally:
        engine.dispose()
