from collections.abc import Iterator
from pathlib import Path
from shutil import copyfile

import anyio
import pytest
from a13n_service.database.config import MigrationConfig
from a13n_service.database.migration import (
    MIGRATIONS_PATH,
    DatabaseMigrator,
    MigrationGraphError,
)
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import sync_database_url
from sqlalchemy import create_engine, inspect, text
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


@pytest.fixture(params=["sqlite", "postgresql"])
def migration_database(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[PostgreSQLConfig | SQLiteConfig]:
    if request.param == "sqlite":
        config: PostgreSQLConfig | SQLiteConfig = SQLiteConfig(path=tmp_path / "migration.sqlite3")
    else:
        config = PostgreSQLConfig(url=request.getfixturevalue("pg_url"))

    _clean_database(config)
    try:
        yield config
    finally:
        _clean_database(config)


def _clean_database(config: PostgreSQLConfig | SQLiteConfig) -> None:
    engine = create_engine(sync_database_url(config), poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'DROP TABLE IF EXISTS "{_TABLE_NAME}"'))
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    finally:
        engine.dispose()


def _has_table(config: PostgreSQLConfig | SQLiteConfig, name: str) -> bool:
    engine = create_engine(sync_database_url(config), poolclass=NullPool)
    try:
        return inspect(engine).has_table(name)
    finally:
        engine.dispose()


def test_upgrade_check_and_downgrade_on_both_backends(
    migration_database: PostgreSQLConfig | SQLiteConfig,
    migration_history: Path,
) -> None:
    migrator = DatabaseMigrator(migration_database, script_location=migration_history)

    migrator.upgrade()
    assert _has_table(migration_database, _TABLE_NAME)
    migrator.current(check_heads=True, verbose=False)

    migrator.downgrade("base")
    assert not _has_table(migration_database, _TABLE_NAME)


async def test_postgresql_concurrent_runners_serialize(
    pg_url: str,
    migration_history: Path,
) -> None:
    config = PostgreSQLConfig(url=pg_url)
    _clean_database(config)

    def upgrade() -> None:
        DatabaseMigrator(config, script_location=migration_history).upgrade()

    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(anyio.to_thread.run_sync, upgrade)
            tasks.start_soon(anyio.to_thread.run_sync, upgrade)
        assert _has_table(config, _TABLE_NAME)
    finally:
        _clean_database(config)


def test_multiple_heads_fail_before_database_access(tmp_path: Path) -> None:
    history = tmp_path / "migrations"
    versions = history / "versions"
    versions.mkdir(parents=True)
    copyfile(MIGRATIONS_PATH / "env.py", history / "env.py")
    for revision in ("left", "right"):
        (versions / f"{revision}.py").write_text(
            f'''revision = "{revision}"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    pass

def downgrade() -> None:
    pass
'''
        )

    migrator = DatabaseMigrator(SQLiteConfig(path=tmp_path / "unused.sqlite3"), script_location=history)

    with pytest.raises(MigrationGraphError, match="left, right"):
        migrator.verify_history()


def test_empty_accepted_history_is_a_valid_base(tmp_path: Path) -> None:
    migrator = DatabaseMigrator(SQLiteConfig(path=tmp_path / "base.sqlite3"))

    migrator.verify_history()
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)


def test_migrations_reject_in_memory_sqlite() -> None:
    with pytest.raises(ValueError, match="file-backed SQLite"):
        DatabaseMigrator(SQLiteConfig(path=Path(":memory:")))


def test_revision_autogenerates_from_upgraded_postgresql(
    pg_url: str,
    migration_history: Path,
) -> None:
    config = PostgreSQLConfig(url=pg_url)
    _clean_database(config)
    migrator = DatabaseMigrator(config, script_location=migration_history)

    try:
        migrator.upgrade()
        migrator.revision("remove migration probe")

        revisions = sorted((migration_history / "versions").glob("*.py"))
        assert len(revisions) == 2
        assert revisions[-1].name.endswith("_remove_migration_probe.py")
        migrator.verify_history()
    finally:
        _clean_database(config)


def test_postgresql_advisory_lock_wait_is_bounded(pg_url: str) -> None:
    config = PostgreSQLConfig(url=pg_url)
    blocker_engine = create_engine(sync_database_url(config), poolclass=NullPool)

    try:
        with blocker_engine.connect() as blocker:
            blocker.execute(text("SELECT pg_advisory_lock(hashtextextended('a13n-service:relational-schema', 0))"))
            blocker.commit()

            migrator = DatabaseMigrator(
                config,
                MigrationConfig(advisory_lock_timeout_seconds=0.05),
            )
            with pytest.raises(DBAPIError):
                migrator.upgrade()

            blocker.execute(text("SELECT pg_advisory_unlock(hashtextextended('a13n-service:relational-schema', 0))"))
            blocker.commit()
    finally:
        blocker_engine.dispose()
