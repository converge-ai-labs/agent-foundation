"""Native file access must not release the embedded SQLite owner's locks."""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Iterable, Iterator
from contextlib import closing
from pathlib import Path
from threading import Event

import pytest
from a13n_harness.environment.files import FileTextSearchRequest
from a13n_harness.providers.environment.direct_local.files import LocalFileOperator
from a13n_harness.providers.environment.direct_local.protected_files import (
    LocalFileProtection,
    check_local_file_read,
    is_protected_local_file,
    protect_local_files,
)
from a13n_harness.providers.environment.direct_local.shared import _DirectLocalFilePolicy
from a13n_harness_ui import host_files as host_files_module
from a13n_harness_ui.errors import HarnessUiError, StoreIntegrityError
from a13n_harness_ui.host_files import (
    MAX_TEXT_BYTES,
    FileReadRequest,
    HostFiles,
)
from a13n_harness_ui.interactive import attachments
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import database as database_module
from a13n_harness_ui.storage.database import open_database
from a13n_harness_ui.storage.migration import DatabaseMigrator, DatabaseSchemaError
from a13n_harness_ui.thread_files import AttachmentUpload, ThreadFiles
from anyio import fail_after, sleep, sleep_forever
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.anyio


def _storage_paths(path: Path) -> tuple[Path, ...]:
    return tuple(Path(f"{path}{suffix}") for suffix in ("", "-wal", "-shm", "-journal"))


@pytest.fixture
def protected_path(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "data" / "metadata.sqlite3"
    path.parent.mkdir()
    path.write_bytes(b"stored content")
    Path(f"{path}-shm").write_bytes(b"shared memory")
    # Access checks need real filesystem identities, not a migrated database.
    with closing(protect_local_files(_storage_paths(path))):
        yield path


def _alias(target: Path, alias_kind: str, root: Path) -> Path:
    if alias_kind == "direct":
        return target
    if alias_kind == "case":
        alias = target.with_name(target.name.upper())
        if not alias.exists():
            pytest.skip("Requires a case-insensitive filesystem")
        assert alias.samefile(target)
    else:
        alias = root / f"{target.name}.{alias_kind}"
        if alias_kind == "symlink":
            alias.symlink_to(target)
        else:
            alias.hardlink_to(target)
    return alias


def _files(root: Path) -> LocalFileOperator:
    return LocalFileOperator(
        root=root,
        policy=_DirectLocalFilePolicy(max_value_bytes=1024 * 1024),
        execution_id="storage-file-protection-test",
        generation="test",
    )


async def _external_payload(path: Path) -> str:
    result = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-c",
            "import sqlite3, sys\n"
            "connection = sqlite3.connect(sys.argv[1], timeout=2)\n"
            "try:\n"
            "    print(connection.execute('SELECT payload FROM planned_restart').fetchone()[0])\n"
            "finally:\n"
            "    connection.close()\n",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.skipif(os.name != "posix", reason="Regression exercises process-wide POSIX SQLite locks")
@pytest.mark.parametrize("surface", ["search", "preview", "attachment", "stored_attachment", "fork"])
async def test_native_reads_preserve_sqlite_locks_across_external_connection_close(
    tmp_path: Path, surface: str
) -> None:
    path = tmp_path / "metadata.sqlite3"
    (tmp_path / "notes.txt").write_text("checkpoint marker\n")
    files = _files(tmp_path)
    thread_files = ThreadFiles(tmp_path / "thread-files")
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
                if surface == "search":
                    result = await files.search_text(
                        FileTextSearchRequest(root="/", pattern="checkpoint marker", max_matches=10)
                    )
                    assert [match.path for match in result.matches] == ["/notes.txt"]
                elif surface == "preview":
                    host = HostFiles(enabled=True)
                    for target in (path, Path(f"{path}-shm")):
                        with pytest.raises(HarnessUiError) as failure:
                            await host.info(FileReadRequest(path=str(target)))
                        assert failure.value.code == "host_files_permission_denied"
                elif surface == "attachment":
                    for target in (path, Path(f"{path}-shm")):
                        with pytest.raises(PermissionError):
                            attachments.read_attachment(target)
                elif surface == "stored_attachment":
                    for target in (path, Path(f"{path}-shm")):
                        attachment = await thread_files.stage(
                            "source", AttachmentUpload(target.name, b"\0" * target.stat().st_size)
                        )
                        content = (
                            thread_files.directory("source") / "tmp/uploads" / attachment.attachment_id / "content"
                        )
                        content.unlink()
                        content.hardlink_to(target)
                        with pytest.raises(PermissionError):
                            await thread_files.read("source", attachment.attachment_id)
                        with pytest.raises(PermissionError):
                            await thread_files.validate_attachments("source", (attachment.attachment_id,))
                        content.unlink()
                else:
                    source = await thread_files.touch("source")
                    aliases = [source / "tmp" / target.name for target in (path, Path(f"{path}-shm"))]
                    for alias, target in zip(aliases, (path, Path(f"{path}-shm")), strict=True):
                        alias.hardlink_to(target)
                    with pytest.raises(shutil.Error, match="in use by host storage"):
                        async with thread_files.fork("source", "destination"):
                            pytest.fail("A fork must reject protected source files")
                    assert not thread_files.directory("destination").exists()
                    for alias in aliases:
                        alias.unlink()
                assert await _external_payload(path) == "before"

                await owner.execute(text("UPDATE planned_restart SET payload='after' WHERE singleton_id=1"))
                await owner.commit()
                assert await _external_payload(path) == "after"
                assert {sidecar: (sidecar.stat().st_dev, sidecar.stat().st_ino) for sidecar in sidecars} == identities
                assert all(sidecar.stat().st_nlink == 1 for sidecar in sidecars)
                # A new App-side connection must still configure and read after
                # the other interpreter closes, rather than raising IOERR 522.
                async with database.engine.connect() as fresh:
                    assert (await fresh.execute(text("SELECT payload FROM planned_restart"))).scalar_one() == "after"
    finally:
        files.close()
        await thread_files.close()


@pytest.mark.parametrize("filename", ["metadata.json", "content"])
async def test_stored_attachment_reads_reject_protected_aliases(
    tmp_path: Path, protected_path: Path, filename: str
) -> None:
    files = ThreadFiles(tmp_path)
    try:
        attachment = await files.stage("source", AttachmentUpload("notes.txt", b"stored content"))
        target = files.directory("source") / "tmp/uploads" / attachment.attachment_id / filename
        target.unlink()
        target.hardlink_to(protected_path)
        with pytest.raises(PermissionError):
            await files.read("source", attachment.attachment_id)
    finally:
        await files.close()


@pytest.mark.parametrize("alias_kind", ["direct", "symlink", "hardlink", "case"])
async def test_host_file_previews_reject_storage_inode_aliases(
    tmp_path: Path, protected_path: Path, alias_kind: str
) -> None:
    host = HostFiles(enabled=True)
    for target in (protected_path, Path(f"{protected_path}-shm")):
        alias = _alias(target, alias_kind, tmp_path)
        # Stat-only metadata remains safe; previews and streams do not.
        assert (await host.metadata(str(alias))).kind == ("symlink" if alias_kind == "symlink" else "file")
        for operation in (host.info, host.read_text, host.open_stream):
            with pytest.raises(HarnessUiError) as failure:
                await operation(FileReadRequest(path=str(alias)))
            assert failure.value.code == "host_files_permission_denied"


@pytest.mark.parametrize("alias_kind", ["direct", "symlink", "hardlink", "case"])
def test_attachment_readers_reject_storage_inode_aliases(
    tmp_path: Path, protected_path: Path, alias_kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_open(*args, **kwargs):
        pytest.fail("Attachment readers must reject protected files before opening them")

    monkeypatch.setattr(Path, "open", unexpected_open)
    for target in (protected_path, Path(f"{protected_path}-shm")):
        alias = _alias(target, alias_kind, tmp_path)
        for reader in (attachments.read_attachment, attachments.read_image):
            with pytest.raises(PermissionError):
                reader(alias)


def test_clipboard_file_list_uses_attachment_protection(
    tmp_path: Path, protected_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ordinary = tmp_path / "notes.txt"
    ordinary.write_bytes(b"project notes")
    monkeypatch.setattr(attachments.ImageGrab, "grabclipboard", lambda: [str(ordinary)])
    assert attachments.clipboard_images()[0].data == b"project notes"
    monkeypatch.setattr(attachments.ImageGrab, "grabclipboard", lambda: [str(protected_path)])
    with pytest.raises(ValueError, match="Clipboard content could not be read") as failure:
        attachments.clipboard_images()
    assert isinstance(failure.value.__cause__, PermissionError)


async def test_other_project_sqlite_files_remain_readable(tmp_path: Path, protected_path: Path) -> None:
    assert is_protected_local_file(protected_path)
    project_path = tmp_path / "project.sqlite3"
    with closing(sqlite3.connect(project_path)) as project:
        project.execute("CREATE TABLE project_data (value)")
        project.execute("INSERT INTO project_data VALUES ('project checkpoint')")
        project.commit()
    header = b"SQLite format 3\x00"
    files = _files(tmp_path)
    host = HostFiles(enabled=True)
    try:
        assert await files.read_bytes("/project.sqlite3", length=len(header)) == header
        assert (await host.info(FileReadRequest(path=str(project_path)))).entry.kind == "file"
        assert (await host.read_text(FileReadRequest(path=str(project_path)))).presentation == "binary"
        assert attachments.read_attachment(project_path).data.startswith(header)
        stream = await host.open_stream(FileReadRequest(path=str(project_path)))
        try:
            assert await host.read_stream(stream, 0, len(header)) == header
        finally:
            await stream.close()
    finally:
        files.close()


async def test_metadata_and_oversize_text_fallback_do_not_open_protected_files(
    protected_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protected_path.write_bytes(b"\0" * (MAX_TEXT_BYTES + 1))

    def unexpected_open(*args, **kwargs):
        pytest.fail("Metadata and an oversized text fallback must not open content")

    monkeypatch.setattr(host_files_module, "_open_stream", unexpected_open)
    host = HostFiles(enabled=True)
    assert (await host.metadata(str(protected_path))).size == MAX_TEXT_BYTES + 1
    assert len((await host.browse(str(protected_path.parent))).entries) == 2
    result = await host.read_text(FileReadRequest(path=str(protected_path)))
    assert result.presentation == "too_large" and result.text is None


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


@pytest.mark.parametrize("operation", ["upgrade", "verify_current"])
async def test_cancelled_startup_keeps_protection_until_sqlite_worker_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    path = tmp_path / "metadata.sqlite3"
    paths = _storage_paths(path)
    started, release, finished = Event(), Event(), Event()

    def blocked_operation(migrator: DatabaseMigrator) -> None:
        try:
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("BEGIN")
                connection.execute("SELECT * FROM sqlite_master").fetchall()
                started.set()
                assert release.wait(10), "Test did not release the SQLite worker"
        finally:
            finished.set()

    async def start_database() -> None:
        async with open_database(path, StorageSettings(data_root=tmp_path)):
            pytest.fail("Cancelled startup must not expose a database")

    monkeypatch.setattr(DatabaseMigrator, operation, blocked_operation)
    task = asyncio.create_task(start_database())
    try:
        assert await asyncio.to_thread(started.wait, 5), "SQLite worker did not start"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert not finished.is_set()
        for item in paths:
            with pytest.raises(PermissionError):
                check_local_file_read(item)
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 5), "SQLite worker did not close"
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    # The worker's finalizer can run just after its operation returns.
    with fail_after(5):
        while any(is_protected_local_file(item) for item in paths):
            await sleep(0)


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
    driver_connection = None
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
                    driver_connection = (await escaped.get_raw_connection()).driver_connection
        assert leases
        assert all(is_protected_local_file(item) for item in paths)
    finally:
        if escaped is not None:
            # dispose() replaced the pool; returning this connection to the old
            # pool would leave its SQLite worker alive beyond the test loop.
            await escaped.invalidate()
            await escaped.close()
        if engine is not None:
            await engine.dispose()
        for lease in leases:
            lease.close()
    if driver_connection is not None:
        with pytest.raises(ValueError, match="no active connection"):
            await driver_connection.execute("SELECT 1")
    assert not any(is_protected_local_file(item) for item in paths)
