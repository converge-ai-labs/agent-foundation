from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest
from a13n_harness.environment import (
    FileQueryRequest,
    FileTextSearchRequest,
)
from a13n_harness.providers.environment.files import FileOperator


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass(frozen=True, slots=True)
class FileSearchConformance:
    def populate(self, root: Path) -> None:
        (root / "src").mkdir()
        (root / "ignored").mkdir()
        (root / ".gitignore").write_text("ignored/\nsrc/generated/\n", encoding="utf-8", newline="\n")
        (root / "src" / "match.py").write_text(
            "before\nneedle one\nafter\nneedle two\n",
            encoding="utf-8",
            newline="\n",
        )
        (root / "src" / "other.txt").write_text("needle\n", encoding="utf-8", newline="\n")
        (root / "src" / "generated").mkdir()
        (root / "src" / "generated" / "ignored.py").write_text("needle\n", encoding="utf-8", newline="\n")
        (root / "ignored" / "hidden.py").write_text("needle\n", encoding="utf-8", newline="\n")

    async def assert_operator(self, files: FileOperator, *, root: str) -> None:
        prefix = PurePosixPath(root)
        expected_path = str(prefix / "src/match.py")
        queried = await files.query(
            FileQueryRequest(
                root=root,
                pattern="*.py",
                ignore_mode="git",
                kinds=frozenset({"file"}),
                max_results=10,
            )
        )
        assert [entry.path for entry in queried.entries] == [expected_path]
        assert queried.has_more is False

        queried_subroot = await files.query(
            FileQueryRequest(
                root=str(prefix / "src"),
                pattern="*.py",
                ignore_mode="git",
                kinds=frozenset({"file"}),
                max_results=10,
            )
        )
        assert [entry.path for entry in queried_subroot.entries] == [expected_path]

        searched = await files.search_text(
            FileTextSearchRequest(
                root=root,
                pattern="needle",
                include="**/*.py",
                ignore_mode="git",
                context_lines=1,
                max_matches=10,
                max_matches_per_file=1,
                max_files=10,
                max_file_bytes=4_096,
                max_line_length=80,
            )
        )
        assert len(searched.matches) == 1
        match = searched.matches[0]
        assert match.path == expected_path
        assert match.line == 2
        assert match.text == "needle one"
        assert match.context == "before\nneedle one\nafter\n"
        assert match.context_start_line == 1
        assert searched.has_more is False


@pytest.fixture
def file_search_conformance() -> FileSearchConformance:
    return FileSearchConformance()
