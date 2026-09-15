from pathlib import Path
from shutil import copyfile

import anyio
import pytest
from a13n_service.database.config import MigrationConfig
from a13n_service.database.default_comparison import compare_server_default
from a13n_service.database.migration import MIGRATIONS_PATH, DatabaseMigrator, MigrationGraphError
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import database_url
from sqlalchemy import JSON, Column, Integer, create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.anyio

_TABLE_NAME = "migration_runner_probe"
_REVISION = "000000000001"


@pytest.fixture
def migration_history(tmp_path: Path) -> Path:
    history = tmp_path / "migrations"
    versions = history / "versions"
    versions.mkdir(parents=True)
    copyfile(MIGRATIONS_PATH / "env.py", history / "env.py")
    copyfile(MIGRATIONS_PATH / "script.py.mako", history / "script.py.mako")
    (versions / "000000000001_create_probe.py").write_text(
        f'''"""Create the migration runner probe."""

from alembic import op
import sqlalchemy as sa

revision = "{_REVISION}"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "{_TABLE_NAME}",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_{_TABLE_NAME}"),
    )


def downgrade() -> None:
    op.drop_table("{_TABLE_NAME}")
'''
    )
    return history


def _has_table(config: PostgreSQLConfig, name: str) -> bool:
    engine = create_engine(database_url(config), poolclass=NullPool)
    try:
        return inspect(engine).has_table(name)
    finally:
        engine.dispose()


def test_upgrade_check_and_downgrade(postgres_database: PostgreSQLConfig, migration_history: Path) -> None:
    migrator = DatabaseMigrator(postgres_database, script_location=migration_history)

    migrator.upgrade()
    assert _has_table(postgres_database, _TABLE_NAME)
    migrator.current(check_heads=True, verbose=False)

    migrator.downgrade("base")
    assert not _has_table(postgres_database, _TABLE_NAME)


async def test_concurrent_runners_serialize(postgres_database: PostgreSQLConfig, migration_history: Path) -> None:
    def upgrade() -> None:
        DatabaseMigrator(postgres_database, script_location=migration_history).upgrade()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(anyio.to_thread.run_sync, upgrade)
        tasks.start_soon(anyio.to_thread.run_sync, upgrade)
    assert _has_table(postgres_database, _TABLE_NAME)


def test_multiple_heads_fail_before_database_access(tmp_path: Path) -> None:
    history = tmp_path / "migrations"
    versions = history / "versions"
    versions.mkdir(parents=True)
    copyfile(MIGRATIONS_PATH / "env.py", history / "env.py")
    for revision in ("left", "right"):
        (versions / f"{revision}.py").write_text(
            f"""revision = "{revision}"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    pass

def downgrade() -> None:
    pass
"""
        )

    migrator = DatabaseMigrator(
        PostgreSQLConfig(url="postgresql+psycopg://unused:5432/unused"), script_location=history
    )

    with pytest.raises(MigrationGraphError, match="left, right"):
        migrator.verify_history()


def test_empty_accepted_history_is_a_valid_base(postgres_database: PostgreSQLConfig) -> None:
    migrator = DatabaseMigrator(postgres_database)

    migrator.verify_history()
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)


def test_json_server_defaults_compare_without_database_json_equality() -> None:
    column = Column("payload", JSON)

    assert compare_server_default(None, column, column, "('[]'::json)", None, "'[]'") is False
    assert compare_server_default(None, column, column, "'{}'::jsonb", None, "'[]'") is True
    assert compare_server_default(None, Column("value", Integer), Column("value", Integer), "1", None, "1") is None


def test_revision_autogenerates_from_upgraded_postgresql(
    postgres_database: PostgreSQLConfig, migration_history: Path
) -> None:
    migrator = DatabaseMigrator(postgres_database, script_location=migration_history)

    migrator.upgrade()
    migrator.revision("remove migration probe")

    revisions = sorted((migration_history / "versions").glob("*.py"))
    assert len(revisions) == 2
    assert revisions[-1].name.endswith("_remove_migration_probe.py")
    migrator.verify_history()


def test_advisory_lock_wait_is_bounded(postgres_database: PostgreSQLConfig) -> None:
    blocker_engine = create_engine(database_url(postgres_database), poolclass=NullPool)

    try:
        with blocker_engine.connect() as blocker:
            blocker.execute(text("SELECT pg_advisory_lock(hashtextextended('a13n-service:relational-schema', 0))"))
            blocker.commit()

            migrator = DatabaseMigrator(postgres_database, MigrationConfig(advisory_lock_timeout_seconds=0.05))
            with pytest.raises(DBAPIError):
                migrator.upgrade()

            blocker.execute(text("SELECT pg_advisory_unlock(hashtextextended('a13n-service:relational-schema', 0))"))
            blocker.commit()
    finally:
        blocker_engine.dispose()
