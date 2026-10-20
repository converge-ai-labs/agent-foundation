from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from a13n_harness.environment.files import FileQueryRequest, FileTextSearchRequest
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment.direct_local.files import LocalFileOperator
from a13n_harness.providers.environment.direct_local.protected_files import (
    check_local_file_read,
    is_protected_local_file,
    protect_local_files,
)
from a13n_harness.providers.environment.direct_local.shared import _DirectLocalFilePolicy

pytestmark = pytest.mark.anyio


def _files(root: Path) -> LocalFileOperator:
    return LocalFileOperator(
        root=root,
        policy=_DirectLocalFilePolicy(max_value_bytes=1024),
        execution_id="test",
        generation="test",
    )


@pytest.fixture
def prevent_protected_open(monkeypatch: pytest.MonkeyPatch) -> None:
    original_open = io.open

    def checked_open(path, *args, **kwargs):
        if not isinstance(path, int):
            assert not is_protected_local_file(Path(path)), "protected inode reached raw open"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(io, "open", checked_open)


def test_independent_owners_and_late_sidecar_alias(tmp_path: Path) -> None:
    database = tmp_path / "metadata.sqlite3"
    sidecar = tmp_path / "metadata.sqlite3-shm"
    first = protect_local_files((database, sidecar))
    second = protect_local_files((database,))
    try:
        assert is_protected_local_file(database)
        assert is_protected_local_file(sidecar)
        sidecar.write_bytes(b"owner creates a sidecar after registration")
        alias = tmp_path / "ordinary.txt"
        os.link(sidecar, alias)
        assert is_protected_local_file(alias)
        with pytest.raises(PermissionError):
            check_local_file_read(alias)
        assert not is_protected_local_file(tmp_path)
        first.close()
        first.close()
        assert is_protected_local_file(database)
        assert not is_protected_local_file(sidecar)
        assert not is_protected_local_file(alias)
    finally:
        first.close()
        second.close()
    assert not is_protected_local_file(database)


def test_unavailable_inode_is_not_an_alias(tmp_path: Path, monkeypatch) -> None:
    protected = tmp_path / "database"
    ordinary = tmp_path / "notes"
    protected.touch()
    ordinary.touch()
    original_stat = Path.stat

    def without_inode(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        fields = list(result)
        fields[1] = 0
        return os.stat_result(fields)

    lease = protect_local_files((protected,))
    try:
        monkeypatch.setattr(Path, "stat", without_inode)
        assert is_protected_local_file(protected)
        check_local_file_read(ordinary)
    finally:
        lease.close()


@pytest.mark.parametrize("alias_kind", ["name", "symlink", "hardlink"])
@pytest.mark.parametrize(
    "operation",
    ["text", "bytes", "stream", "search", "patch", "append", "copy"],
)
async def test_direct_file_reads_denied_before_open(
    tmp_path: Path, prevent_protected_open, alias_kind, operation
) -> None:
    protected = tmp_path / "metadata.sqlite3"
    protected.write_text("private storage\n")
    alias = tmp_path / "alias.txt"
    if alias_kind == "symlink":
        alias.symlink_to(protected)
    elif alias_kind == "hardlink":
        os.link(protected, alias)
    else:
        alias = protected
    lease = protect_local_files((protected,))
    files = _files(tmp_path)
    selected = "/" + alias.name
    try:
        with pytest.raises(EnvironmentError) as failure:
            if operation == "text":
                await files.read_text(selected)
            elif operation == "bytes":
                await files.read_bytes(selected)
            elif operation == "stream":
                async for _ in files.read_bytes_stream(selected):
                    pytest.fail("protected bytes were streamed")
            elif operation == "search":
                await files.search_text(FileTextSearchRequest(root=selected, pattern="private", max_matches=10))
            elif operation == "patch":
                await files.patch_text(selected, "--- a/file\n+++ b/file\n@@ -1 +1 @@\n-private storage\n+new\n")
            elif operation == "append":
                await files.write_text(selected, "extra", mode="append")
            elif operation == "copy":
                await files.copy(selected, "/copy.txt")
        assert failure.value.code == "environment_denied"
        assert not (tmp_path / "copy.txt").exists()
        assert not list(tmp_path.glob(".a13n-write-*"))
    finally:
        files.close()
        lease.close()
    assert protected.read_text() == "private storage\n"


async def test_recursive_search_and_gitignore_skip_protected_aliases(tmp_path: Path, prevent_protected_open) -> None:
    data = tmp_path / "data"
    data.mkdir()
    database = data / "metadata.sqlite3"
    database.write_text("needle private\n")
    # The .gitignore parser opens files even for metadata-only file queries.
    os.link(database, tmp_path / ".gitignore")
    os.link(database, tmp_path / "alias.txt")
    ordinary = tmp_path / "project.sqlite3"
    ordinary.write_bytes(b"needle public\n")
    lease = protect_local_files((database,))
    files = _files(tmp_path)
    try:
        result = await files.search_text(
            FileTextSearchRequest(
                root="/", pattern="needle", include_hidden=True, ignore_mode="git", max_files=1, max_matches=10
            )
        )
        assert [match.path for match in result.matches] == ["/project.sqlite3"]
        query = await files.query(FileQueryRequest(root="/", pattern="**", ignore_mode="git", max_results=10))
        assert any(entry.path == "/project.sqlite3" for entry in query.entries)
        assert await files.read_bytes("/project.sqlite3") == b"needle public\n"
    finally:
        files.close()
        lease.close()


async def test_metadata_and_filtered_reads_do_not_open_protected_files(tmp_path: Path, prevent_protected_open) -> None:
    path = tmp_path / "metadata.sqlite3"
    path.write_bytes(b"x" * 1025)
    lease = protect_local_files((path,))
    files = _files(tmp_path)
    try:
        assert (await files.stat("/metadata.sqlite3")).size == 1025
        assert [entry.path for entry in (await files.list("/", max_results=10)).entries] == ["/metadata.sqlite3"]
        with pytest.raises(EnvironmentError) as failure:
            await files.read_bytes("/metadata.sqlite3")
        assert failure.value.code == "environment_too_large"
        for filters in ({"max_file_bytes": 1024}, {"include": "*.py"}):
            result = await files.search_text(
                FileTextSearchRequest(root="/metadata.sqlite3", pattern="x", max_matches=10, **filters)
            )
            assert result.matches == ()
        with pytest.raises(EnvironmentError) as failure:
            await files.read_bytes("/metadata.sqlite3", length=1)
        assert failure.value.code == "environment_denied"
    finally:
        files.close()
        lease.close()


async def test_existing_case_alias_is_protected(tmp_path: Path, prevent_protected_open) -> None:
    path = tmp_path / "metadata.sqlite3"
    path.write_text("private storage")
    alias = path.with_name(path.name.upper())
    if not alias.exists():
        pytest.skip("Requires a case-insensitive filesystem")
    assert alias.samefile(path)
    lease = protect_local_files((path,))
    files = _files(tmp_path)
    try:
        with pytest.raises(EnvironmentError) as failure:
            await files.read_bytes("/" + alias.name)
        assert failure.value.code == "environment_denied"
    finally:
        files.close()
        lease.close()
