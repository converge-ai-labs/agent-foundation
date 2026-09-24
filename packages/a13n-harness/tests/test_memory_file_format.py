from __future__ import annotations

import pytest
from a13n_harness.providers.memory import FileFormat, MemoryStoreError, describe, validate_directory, validate_path

FORMAT = FileFormat(max_file_bytes=256, description_chars=20, frontmatter_bytes=64, path_bytes=16)


@pytest.mark.parametrize("path", ["notes.md", "a/b/c.md", ".hidden", "café.md", "x" * 16])
def test_valid_paths_are_canonical(path: str) -> None:
    assert validate_path(path, FORMAT) == path


def test_paths_are_nfc_normalized() -> None:
    assert validate_path("café.md", FORMAT) == "café.md"


@pytest.mark.parametrize(
    "path",
    ["", "/abs.md", "dir/", "a//b.md", "./a.md", "a/../b.md", "..", "a\\b.md", "tab\t.md", "nul\x00.md", "x" * 17],
)
def test_invalid_paths_are_rejected(path: str) -> None:
    with pytest.raises(MemoryStoreError) as caught:
        validate_path(path, FORMAT)
    assert caught.value.code == "invalid_path"


def test_path_limit_counts_utf8_bytes() -> None:
    with pytest.raises(MemoryStoreError):
        validate_path("é" * 9, FORMAT)


@pytest.mark.parametrize(("path", "expected"), [("", ""), ("notes/", "notes/"), ("a/b/", "a/b/")])
def test_directories_are_empty_or_end_with_a_slash(path: str, expected: str) -> None:
    assert validate_directory(path, FORMAT) == expected


@pytest.mark.parametrize("path", ["notes", "/notes/", "a/../", "a//"])
def test_invalid_directories_are_rejected(path: str) -> None:
    with pytest.raises(MemoryStoreError) as caught:
        validate_directory(path, FORMAT)
    assert caught.value.code == "invalid_path"


@pytest.mark.parametrize(
    ("text", "description"),
    [
        ("---\ndescription: Tea preferences\n---\nlikes tea\n", "Tea preferences"),
        ("---\ntags: [a]\n---\n\n  first line  \nsecond\n", "first line"),
        ("---\n---\nbody\n", "body"),
        ("\n\nplain first line\n", "plain first line"),
        ("a very long first line that is cut\n", "a very long first li"),
        ("", None),
        ("---\ndescription: null\n---\n", None),
    ],
)
def test_description_comes_from_frontmatter_or_the_first_line(text: str, description: str | None) -> None:
    assert describe(text, FORMAT) == description


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("x" * 257, "too_large"),
        ("---\ndescription: unclosed\n", "invalid_file"),
        ("---\n" + "k: v\n" * 20 + "---\n", "invalid_file"),
        ("---\n[unbalanced\n---\n", "invalid_file"),
        ("---\n- a list\n---\n", "invalid_file"),
        ("---\ndescription: 7\n---\n", "invalid_file"),
        ("---\ndescription: '   '\n---\n", "invalid_file"),
        ("---\ndescription: |\n  two\n  lines\n---\n", "invalid_file"),
        ("---\ndescription: longer than twenty characters\n---\n", "invalid_file"),
    ],
)
def test_invalid_files_are_rejected(text: str, code: str) -> None:
    with pytest.raises(MemoryStoreError) as caught:
        describe(text, FORMAT)
    assert caught.value.code == code
