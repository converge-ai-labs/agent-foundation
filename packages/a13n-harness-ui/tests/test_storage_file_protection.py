"""Native file access must not release the embedded SQLite owner's locks."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path

import pytest
from a13n_harness.environment.files import FileTextSearchRequest
from a13n_harness.providers.environment.direct_local import files as local_files
from a13n_harness.providers.environment.direct_local.files import LocalFileOperator
from a13n_harness.providers.environment.direct_local.protected_files import (
    LocalFileProtection,
    is_protected_local_file,
    protect_local_files,
)
from a13n_harness.providers.environment.direct_local.shared import _DirectLocalFilePolicy
from a13n_harness_ui.errors import HarnessUiError, StoreIntegrityError
from a13n_harness_ui.host_files import (
    MAX_TEXT_BYTES,
    FileDeleteRequest,
    FileMoveRequest,
    FileReadRequest,
    FileWriteRequest,
    HostFiles,
)
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import database as database_module
from a13n_harness_ui.storage.database import open_database
from a13n_harness_ui.storage.migration import DatabaseMigrator, DatabaseSchemaError
from anyio import sleep_forever
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.anyio


def _storage_paths(path: Path) -> tuple[Path, ...]:
    return tuple(Path(f"{path}{suffix}") for suffix in ("", "-wal", "-shm", "-journal"))


def _files(root: Path) -> LocalFileOperator:
    return LocalFileOperator(
        root=root,
        policy=_DirectLocalFilePolicy(max_value_bytes=1024 * 1024),
        execution_id="storage-file-protection-test",
        generation="test",
    )


async def _external_payload(path: Path) -> str:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import sqlite3, sys\n"
        "connection = sqlite3.connect(sys.argv[1], timeout=2)\n"
        "try:\n"
        "    print(connection.execute('SELECT payload FROM planned_restart').fetchone()[0])\n"
        "finally:\n"
        "    connection.close()\n",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, stderr.decode()
    return stdout.decode().strip()


@pytest.mark.skipif(os.name != "posix", reason="Regression exercises process-wide POSIX SQLite locks")
async def test_native_search_preserves_sqlite_locks_across_external_connection_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "metadata.sqlite3"
    (tmp_path / "notes.txt").write_text("checkpoint marker\n")
    scanned: list[Path] = []
    search_file = local_files.search_text_file

    def record_search(native: Path, *args, **kwargs):
        scanned.append(native)
        return search_file(native, *args, **kwargs)

    monkeypatch.setattr(local_files, "search_text_file", record_search)
    files = _files(tmp_path)
    try:
        async with open_database(path, StorageSettings(data_root=tmp_path)) as database:
            async with database.engine.connect() as owner:
                await owner.execute(text("PRAGMA wal_autocheckpoint=0"))
                await owner.execute(text("INSERT INTO planned_restart (singleton_id, payload) VALUES (1, 'before')"))
                await owner.commit()
                sidecars = _storage_paths(path)[1:3]
                identities = {sidecar: (sidecar.stat().st_dev, sidecar.stat().st_ino) for sidecar in sidecars}

                # Before the guard, even a binary-file scan opens and closes the
                # database and SHM, dropping this process's SQLite locks.
                result = await files.search_text(
                    FileTextSearchRequest(root="/", pattern="checkpoint marker", max_matches=10)
                )
                assert [match.path for match in result.matches] == ["/notes.txt"]
                assert await _external_payload(path) == "before"

                await owner.execute(text("UPDATE planned_restart SET payload='after' WHERE singleton_id=1"))
                await owner.commit()
                assert await _external_payload(path) == "after"
                assert {sidecar: (sidecar.stat().st_dev, sidecar.stat().st_ino) for sidecar in sidecars} == identities
                assert all(sidecar.stat().st_nlink == 1 for sidecar in sidecars)
                assert not set(_storage_paths(path)).intersection(scanned)
                assert tmp_path / "notes.txt" in scanned

                # A new App-side connection must still configure and read after
                # the other interpreter closes, rather than raising IOERR 522.
                async with database.engine.connect() as fresh:
                    assert (await fresh.execute(text("SELECT payload FROM planned_restart"))).scalar_one() == "after"
    finally:
        files.close()


@pytest.mark.parametrize("alias_kind", ["direct", "symlink", "hardlink"])
async def test_host_file_previews_reject_storage_inode_aliases(tmp_path: Path, alias_kind: str) -> None:
    path = tmp_path / "metadata.sqlite3"
    host = HostFiles(enabled=True)
    async with open_database(path, StorageSettings(data_root=tmp_path)) as database:
        async with database.engine.connect() as owner:
            await owner.execute(text("INSERT INTO planned_restart (singleton_id, payload) VALUES (1, 'kept')"))
            await owner.commit()
            for target in (path, Path(f"{path}-shm")):
                alias = target if alias_kind == "direct" else tmp_path / f"{target.name}.{alias_kind}"
                if alias_kind == "symlink":
                    alias.symlink_to(target)
                elif alias_kind == "hardlink":
                    alias.hardlink_to(target)
                # Stat-only metadata remains safe; previews and streams do not.
                assert (await host.metadata(str(alias))).kind == ("symlink" if alias_kind == "symlink" else "file")
                for operation in (host.info, host.read_text, host.open_stream):
                    with pytest.raises(HarnessUiError) as failure:
                        await operation(FileReadRequest(path=str(alias)))
                    assert failure.value.code == "host_files_permission_denied"
            assert (await owner.execute(text("SELECT payload FROM planned_restart"))).scalar_one() == "kept"


async def test_other_project_sqlite_files_remain_readable(tmp_path: Path) -> None:
    project_path = tmp_path / "project.sqlite3"
    with closing(sqlite3.connect(project_path)) as project:
        project.execute("CREATE TABLE project_data (value)")
        project.execute("INSERT INTO project_data VALUES ('project checkpoint')")
        project.commit()
    header = b"SQLite format 3\x00"
    files = _files(tmp_path)
    host = HostFiles(enabled=True)
    try:
        async with open_database(tmp_path / "metadata.sqlite3", StorageSettings(data_root=tmp_path)):
            assert await files.read_bytes("/project.sqlite3", length=len(header)) == header
            assert (await host.info(FileReadRequest(path=str(project_path)))).entry.kind == "file"
            assert (await host.read_text(FileReadRequest(path=str(project_path)))).presentation == "binary"
            stream = await host.open_stream(FileReadRequest(path=str(project_path)))
            try:
                assert await host.read_stream(stream, 0, len(header)) == header
            finally:
                await stream.close()
    finally:
        files.close()


async def test_large_protected_file_is_denied_before_text_size_fallback(tmp_path: Path) -> None:
    path = tmp_path / "large.sqlite3"
    path.write_bytes(b"\0" * (MAX_TEXT_BYTES + 1))
    protection = protect_local_files((path,))
    try:
        with pytest.raises(HarnessUiError) as failure:
            await HostFiles(enabled=True).read_text(FileReadRequest(path=str(path)))
        assert failure.value.code == "host_files_permission_denied"
    finally:
        protection.close()


@pytest.mark.parametrize(
    "operation", ["write", "write_text", "move", "delete", "move_parent", "delete_parent", "journal"]
)
async def test_host_mutations_leave_live_database_and_neighbors_intact(tmp_path: Path, operation: str) -> None:
    data = tmp_path / "data"
    data.mkdir()
    path = data / "metadata.sqlite3"
    neighbor = data / "ordinary.txt"
    neighbor.write_text("preserve")
    host = HostFiles(enabled=True)
    async with open_database(path, StorageSettings(data_root=data)) as database:
        async with database.engine.connect() as owner:
            await owner.execute(text("INSERT INTO planned_restart (singleton_id, payload) VALUES (1, 'kept')"))
            await owner.commit()
            selected = data if operation.endswith("_parent") else path
            observed = await host.metadata(str(selected))
            with pytest.raises(HarnessUiError) as failure:
                if operation == "write":
                    await host.write(str(path), b"replacement", expected_revision=observed.revision)
                elif operation == "write_text":
                    await host.write_text(
                        FileWriteRequest(path=str(path), text="replacement", expected_revision=observed.revision)
                    )
                elif operation in {"move", "move_parent"}:
                    await host.move(
                        FileMoveRequest(
                            path=str(selected), destination=str(tmp_path / "moved"), expected_revision=observed.revision
                        )
                    )
                elif operation in {"delete", "delete_parent"}:
                    await host.delete(
                        FileDeleteRequest(path=str(selected), expected_revision=observed.revision, recursive=True)
                    )
                else:
                    await host.write(f"{path}-journal", b"not a journal", expected_revision=None)
            assert failure.value.code == "host_files_permission_denied"
            assert (await owner.execute(text("SELECT payload FROM planned_restart"))).scalar_one() == "kept"
            assert neighbor.read_text() == "preserve"
            assert not (tmp_path / "moved").exists()
            assert not Path(f"{path}-journal").exists()


@pytest.mark.parametrize("migration_fails", [False, True])
async def test_file_protection_precedes_migration_and_releases_after_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, migration_fails: bool
) -> None:
    path = tmp_path / "metadata.sqlite3"
    paths = _storage_paths(path)
    upgrade = DatabaseMigrator.upgrade
    migration_entered = False

    def checked_upgrade(migrator: DatabaseMigrator) -> None:
        nonlocal migration_entered
        migration_entered = True
        assert all(is_protected_local_file(item) for item in paths)
        if migration_fails:
            raise DatabaseSchemaError("synthetic migration failure")
        upgrade(migrator)

    monkeypatch.setattr(DatabaseMigrator, "upgrade", checked_upgrade)
    if migration_fails:
        with pytest.raises(StoreIntegrityError) as failure:
            async with open_database(path, StorageSettings(data_root=tmp_path)):
                pytest.fail("A failed migration must not expose a database")
        assert failure.value.code == "database_migration_failed"
    else:
        async with open_database(path, StorageSettings(data_root=tmp_path)):
            assert all(is_protected_local_file(item) for item in paths)
    assert migration_entered
    assert not any(is_protected_local_file(item) for item in paths)


@pytest.mark.parametrize("cleanup", ["timeout", "checked_out"])
async def test_file_protection_survives_incomplete_connection_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup: str
) -> None:
    path = tmp_path / "metadata.sqlite3"
    paths = _storage_paths(path)
    leases: list[LocalFileProtection] = []

    def capture_protection(items: Iterable[Path]) -> LocalFileProtection:
        lease = protect_local_files(items)
        leases.append(lease)
        return lease

    async def blocked_dispose(engine: AsyncEngine) -> None:
        await sleep_forever()

    monkeypatch.setattr(database_module, "protect_local_files", capture_protection)
    engine = None
    escaped = None
    settings = StorageSettings(data_root=tmp_path, cleanup_timeout_seconds=0.01)
    try:
        with monkeypatch.context() as patch:
            if cleanup == "timeout":
                patch.setattr(AsyncEngine, "dispose", blocked_dispose)
            async with open_database(path, settings) as database:
                engine = database.engine
                if cleanup == "checked_out":
                    escaped = await engine.connect()
                    await escaped.execute(text("SELECT 1"))
        assert leases
        assert all(is_protected_local_file(item) for item in paths)
    finally:
        if escaped is not None:
            await escaped.close()
        if engine is not None:
            await engine.dispose()
        for lease in leases:
            lease.close()
    assert not any(is_protected_local_file(item) for item in paths)
