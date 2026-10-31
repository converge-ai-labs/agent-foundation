"""The migrated test template preserves independent files and the real storage lifecycle."""

from pathlib import Path

import pytest
from a13n_harness_ui.errors import StoreIntegrityError
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import database
from a13n_harness_ui.storage.migration import DatabaseMigrator
from sqlalchemy import text

pytestmark = pytest.mark.anyio


@pytest.mark.usefixtures("migrated_databases")
async def test_template_files_are_independent_and_reopening_preserves_changes(tmp_path: Path) -> None:
    first, second = tmp_path / "first.sqlite3", tmp_path / "second.sqlite3"
    settings = StorageSettings(data_root=tmp_path)
    async with database.open_database(first, settings) as opened:
        async with opened.engine.begin() as connection:
            await connection.execute(text("CREATE TABLE fixture_marker (value TEXT)"))
            await connection.execute(text("INSERT INTO fixture_marker VALUES ('first')"))
    async with database.open_database(second, settings) as opened:
        async with opened.engine.connect() as connection:
            assert not (
                await connection.execute(text("SELECT name FROM sqlite_master WHERE name = 'fixture_marker'"))
            ).all()
    async with database.open_database(first, settings) as opened:
        async with opened.engine.connect() as connection:
            assert (await connection.execute(text("SELECT value FROM fixture_marker"))).scalar_one() == "first"
            assert (await connection.execute(text("PRAGMA synchronous"))).scalar_one() == 2
            assert (await connection.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1


@pytest.mark.usefixtures("migrated_databases")
async def test_template_preserves_real_upgrade_and_rejects_existing_damage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[DatabaseMigrator] = []
    original = DatabaseMigrator.upgrade

    def upgrade(self: DatabaseMigrator) -> None:
        calls.append(self)
        original(self)

    monkeypatch.setattr(DatabaseMigrator, "upgrade", upgrade)
    settings = StorageSettings(data_root=tmp_path)
    async with database.open_database(tmp_path / "fresh.sqlite3", settings):
        pass
    assert len(calls) == 1
    damaged = tmp_path / "damaged.sqlite3"
    damaged.write_bytes(b"not a SQLite database")
    with pytest.raises(StoreIntegrityError):
        async with database.open_database(damaged, settings):
            pytest.fail("A damaged database must not be replaced by the template")
    assert len(calls) == 2
    assert damaged.read_bytes() == b"not a SQLite database"


def test_template_fixture_is_opt_in_and_does_not_patch_the_migration_owner(tmp_path: Path) -> None:
    assert database.DatabaseMigrator is DatabaseMigrator
    path = tmp_path / "unmigrated.sqlite3"
    DatabaseMigrator(path)
    assert not path.exists()


@pytest.mark.usefixtures("migrated_databases")
def test_direct_migrators_still_start_empty(tmp_path: Path) -> None:
    path = tmp_path / "direct.sqlite3"
    DatabaseMigrator(path)
    assert not path.exists()
