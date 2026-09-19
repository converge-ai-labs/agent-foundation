"""Exercise guest filesystem semantics without a network or sandbox dependency."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.providers.environment._guest import files
from a13n_harness.providers.environment._guest_files import GuestFiles
from a13n_harness.providers.environment.e2b.configuration import E2BEnvironmentConfiguration
from a13n_harness.providers.environment.models import EnvironmentError

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Guest filesystem helpers execute on POSIX sandboxes")


@pytest.fixture
def filesystem(tmp_path):
    def execute(action, **arguments):
        return files.execute(
            {
                "configuration": {
                    "root": str(tmp_path),
                    "read_only": False,
                    "max_query_entries": 100,
                    "max_file_bytes": 4096,
                },
                "action": action,
                "arguments": arguments,
            }
        )

    return execute


def test_file_paths_preserve_hidden_names_and_reject_escape(tmp_path, filesystem):
    (tmp_path / ".hidden").write_text("hello")
    assert filesystem("stat", path="/.hidden")["path"] == "/.hidden"
    with pytest.raises(ValueError):
        filesystem("stat", path="/../outside")
    (tmp_path / "escape").symlink_to(tmp_path.parent)
    with pytest.raises(PermissionError):
        filesystem("read", path="/escape/file", offset=0, length=10)
    assert filesystem("stat", path="/escape")["kind"] == "symlink"


def test_no_replace_publication_and_staging(tmp_path, filesystem):
    (tmp_path / "target").write_text("old")
    (tmp_path / "stage").write_text("new")
    with pytest.raises(FileExistsError):
        filesystem("publish", path="/target", staged="/stage", mode="create")
    assert (tmp_path / "target").read_text() == "old"
    filesystem("publish", path="/target", staged="/stage", mode="replace")
    assert (tmp_path / "target").read_text() == "new"
    assert not (tmp_path / "stage").exists()


def test_query_paging_and_bounded_scan(tmp_path, filesystem):
    for name in ("one.py", "two.py", "three.txt", ".hidden.py"):
        (tmp_path / name).write_text(name)
    result = filesystem("query", path="/", pattern="**/*.py", max_results=1, offset=0)
    assert [item["path"] for item in result["entries"]] == ["/one.py"]
    assert result["has_more"]
    result = filesystem("query", path="/", pattern="**/*.py", max_results=1, offset=1)
    assert [item["path"] for item in result["entries"]] == ["/two.py"]
    assert not result["has_more"]
    with pytest.raises(OverflowError):
        list(files.entries(tmp_path, tmp_path, {}, 1))


def test_search_reports_context_truncation_and_page(tmp_path, filesystem):
    (tmp_path / "text").write_text("before\nMATCH-long\nafter\nMATCH\n")
    result = filesystem(
        "search",
        path="/",
        include="**/*",
        pattern="match",
        recursive=True,
        include_hidden=False,
        ignore_mode="none",
        case_sensitive=False,
        regex=False,
        offset=0,
        max_matches=1,
        max_matches_per_file=None,
        max_files=None,
        max_file_bytes=4096,
        max_line_length=5,
        context_lines=1,
    )
    assert result["has_more"]
    assert result["matches"][0]["text"] == "MATCH"
    assert result["matches"][0]["text_truncated"]
    assert result["matches"][0]["context_start_line"] == 1


@pytest.mark.parametrize("action", ["read", "resolve", "publish"])
@pytest.mark.parametrize("kind", ["missing", "directory", "dangling-link"])
def test_regular_file_operations_distinguish_missing_targets_from_wrong_types(tmp_path, filesystem, action, kind):
    if kind == "directory":
        (tmp_path / "target").mkdir()
    elif kind == "dangling-link":
        (tmp_path / "target").symlink_to(tmp_path / "missing")
    (tmp_path / "stage").write_text("candidate")
    arguments = {
        "read": {"offset": 0, "length": 10},
        "resolve": {"regular_file": True},
        "publish": {"staged": "/stage", "mode": "replace"},
    }
    with pytest.raises(ValueError if kind == "directory" else FileNotFoundError):
        filesystem(action, path="/target", **arguments[action])
    assert (tmp_path / "stage").read_text() == "candidate"
    if kind == "directory":
        assert (tmp_path / "target").is_dir()
    elif kind == "dangling-link":
        assert (tmp_path / "target").is_symlink() and not (tmp_path / "missing").exists()
    else:
        assert not (tmp_path / "target").exists()


def test_stream_resolution_authorizes_read_before_returning_native_path(tmp_path, filesystem, monkeypatch):
    from pathlib import Path

    target = tmp_path / "private"
    target.write_text("PRIVATE")
    original_open = Path.open

    def denied(path, *args, **kwargs):
        if path == target:
            raise PermissionError("private OS diagnostic")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(PermissionError):
        filesystem("resolve", path="/private", regular_file=True)


def test_upload_stage_creation_is_exclusive_and_honors_read_only(tmp_path, filesystem):
    result = filesystem("stage", path="/stage")
    assert result == {"path": str(tmp_path / "stage")}
    assert (tmp_path / "stage").read_bytes() == b""
    (tmp_path / "stage").write_text("EXISTING")
    with pytest.raises(FileExistsError):
        filesystem("stage", path="/stage")
    assert (tmp_path / "stage").read_text() == "EXISTING"
    with pytest.raises(PermissionError):
        files.execute(
            {
                "configuration": {"root": str(tmp_path), "read_only": True},
                "action": "stage",
                "arguments": {"path": "/new"},
            }
        )
    assert not (tmp_path / "new").exists()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "code", ["environment_conflict", "environment_denied", "environment_not_found", "environment_request_invalid"]
)
async def test_rejected_stage_creation_never_uploads_or_deletes_an_unowned_entry(tmp_path, code):
    upload = AsyncMock()
    operations = AsyncMock(side_effect=[{"path": str(tmp_path / "stage")}, EnvironmentError("Rejected", code=code)])
    commands = SimpleNamespace(
        configuration=E2BEnvironmentConfiguration(),
        files=operations,
        sandbox=SimpleNamespace(files=SimpleNamespace(write=upload)),
    )
    with pytest.raises(EnvironmentError) as caught:
        await GuestFiles(commands).write_text("/target", "CHANGED", mode="replace")
    assert caught.value.code == code
    assert [call.args[0] for call in operations.await_args_list] == ["resolve", "stage"]
    upload.assert_not_awaited()
