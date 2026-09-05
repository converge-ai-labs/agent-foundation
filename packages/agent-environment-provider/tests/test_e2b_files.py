"""Exercise guest filesystem semantics without a network or sandbox dependency."""

from __future__ import annotations

import sys

import pytest
from a13n_environment_provider.e2b.guest import files

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
