from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from a13n_harness.environment.files import FileQueryRequest, FileTextSearchRequest
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment.direct_local.files import LocalFileOperator
from a13n_harness.providers.environment.direct_local.protected_files import (
    check_local_file_access,
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


async def _bytes():
    yield b"replacement"


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
        assert is_protected_local_file(tmp_path, descendants=True)
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
        check_local_file_access(ordinary)
    finally:
        lease.close()


@pytest.mark.parametrize("alias_kind", ["name", "symlink", "hardlink"])
@pytest.mark.parametrize(
    "operation",
    ["text", "bytes", "stream", "search", "patch", "append", "write", "copy", "move", "remove"],
)
async def test_direct_file_access_denied_before_open(tmp_path: Path, monkeypatch, alias_kind, operation) -> None:
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
    original_open = io.open

    def checked_open(path, *args, **kwargs):
        if not isinstance(path, int):
            assert not is_protected_local_file(Path(path)), "protected inode reached raw open"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(io, "open", checked_open)
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
                await files.patch_text(selected, "unused because access must be rejected first")
            elif operation == "append":
                await files.write_text(selected, "extra", mode="append")
            elif operation == "write":
                await files.write_bytes_stream(selected, _bytes(), mode="replace")
            elif operation == "copy":
                await files.copy(selected, "/copy.txt")
            elif operation == "move":
                await files.move(selected, "/moved")
            elif operation == "remove":
                await files.remove(selected)
        assert failure.value.code == "environment_denied"
        assert not (tmp_path / "copy.txt").exists()
        assert not (tmp_path / "moved").exists()
    finally:
        files.close()
        lease.close()
    assert protected.read_text() == "private storage\n"


async def test_recursive_search_and_gitignore_skip_protected_aliases(tmp_path: Path, monkeypatch) -> None:
    data = tmp_path / "data"
    data.mkdir()
    database = data / "metadata.sqlite3"
    database.write_text("needle private\n")
    # The .gitignore parser opens files even for metadata-only file queries.
    os.link(database, tmp_path / ".gitignore")
    os.link(database, tmp_path / "alias.txt")
    ordinary = tmp_path / "project.sqlite3"
    ordinary.write_text("needle public\n")
    lease = protect_local_files((database,))
    original_open = io.open

    def checked_open(path, *args, **kwargs):
        if not isinstance(path, int):
            assert not is_protected_local_file(Path(path)), "protected inode reached raw open"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(io, "open", checked_open)
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


@pytest.mark.parametrize("operation", ["remove", "move", "replace-directory", "create-sidecar"])
async def test_mutation_preflight_preserves_storage_tree(tmp_path: Path, operation) -> None:
    data = tmp_path / "data"
    data.mkdir()
    database = data / "metadata.sqlite3"
    database.write_text("storage")
    (data / "ordinary").write_text("keep")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    lease = protect_local_files((database, data / "metadata.sqlite3-journal"))
    files = _files(tmp_path)
    try:
        await files.mkdir("/data", exist_ok=True)
        with pytest.raises(EnvironmentError) as failure:
            if operation == "remove":
                await files.remove("/data", recursive=True)
            elif operation == "move":
                await files.move("/data", "/moved")
            elif operation == "replace-directory":
                await files.move("/replacement", "/data", replace=True)
            else:
                await files.write_bytes_stream("/data/metadata.sqlite3-journal", _bytes(), mode="create")
        assert failure.value.code == "environment_denied"
    finally:
        files.close()
        lease.close()
    assert database.read_text() == "storage"
    assert (data / "ordinary").read_text() == "keep"
    assert replacement.is_dir()
    assert not (data / "metadata.sqlite3-journal").exists()
