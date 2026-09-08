"""Cross-provider path, content, paging, and diagnostic regressions."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from a13n_environment import DirectLocalEnvironmentProvider, DirectLocalProviderRuntime
from a13n_environment._file_patterns import PathPattern, PatternError, content_pattern
from a13n_environment.e2b.commands import GuestCommands
from a13n_environment.e2b.configuration import E2BProviderConfiguration
from a13n_environment.e2b.guest.files import execute
from a13n_environment.files import FileQueryRequest, FileTextSearchRequest
from a13n_environment.models import EnvironmentError

CASES = json.loads((Path(__file__).resolve().parents[3] / "crates/a13n-envd/tests/file-patterns.json").read_text())


@pytest.mark.parametrize("case", CASES["glob"], ids=lambda case: case["pattern"])
def test_path_pattern_conformance(case):
    matcher = PathPattern(case["pattern"])
    assert all(matcher.matches(path) for path in case["matches"])
    assert not any(matcher.matches(path) for path in case["misses"])


@pytest.mark.parametrize("pattern", CASES["invalid_glob"] + ["a" * 16385])
def test_path_pattern_diagnostics(pattern):
    with pytest.raises(PatternError) as error:
        PathPattern(pattern, "include")
    assert error.value.details["field"] == "include"
    assert error.value.details["reason"] == "invalid_glob"
    assert "{a,b}" in error.value.details["hint"]


@pytest.mark.parametrize("case", CASES["content"])
def test_content_pattern_conformance(case):
    pattern = content_pattern(case["pattern"], case["regex"], case["case_sensitive"])
    assert bool(pattern.search(case["text"])) == case["matches"]


@pytest.fixture(params=["local", "guest"])
async def search_files(request, tmp_path):
    provider = DirectLocalEnvironmentProvider()
    configuration = provider.validate_configuration(schema_version="1", value={"root": {"path": str(tmp_path)}})
    environment = provider.create_environment(
        environment_id="conformance", configuration=configuration, state=None, runtime=DirectLocalProviderRuntime()
    )
    await environment.enter(thread_id="thread", run_id="run", agent_instance_id="agent", mount_id="mount")
    await environment.prepare()

    async def operation(action, **arguments):
        model = (
            FileQueryRequest(root="/", max_results=arguments.pop("max_results", 100), **arguments)
            if action == "query"
            else FileTextSearchRequest(root="/", max_matches=arguments.pop("max_matches", 100), **arguments)
        )
        if request.param == "local":
            files = environment.operations.files
            assert files is not None
            result = await (files.query(model) if action == "query" else files.search_text(model))
            return result.model_dump(mode="json")
        values = model.model_dump(mode="json")
        values["path"] = values.pop("root")
        return execute(
            {
                "configuration": {
                    "root": str(tmp_path),
                    "read_only": False,
                    "max_query_entries": 1000,
                    "max_file_bytes": 16384,
                },
                "action": action,
                "arguments": values,
            }
        )

    try:
        yield operation
    finally:
        await environment.close()


@pytest.mark.anyio
async def test_provider_braces_anchoring_and_query_include_agree(tmp_path, search_files):
    paths = ["a.py", "src/b.rs", "src/deep/c.py", "tests/d.py", "other/e.txt"]
    for path in paths:
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("needle\n")
    for pattern, expected in [
        ("*.{py,rs}", paths[:4]),
        ("/*.py", paths[:1]),
        ("{src,tests}/**/*.{py,rs}", paths[1:4]),
        ("src/*.rs", paths[1:2]),
    ]:
        query = await search_files("query", pattern=pattern, kinds=["file"])
        search = await search_files("search", pattern="needle", include=pattern)
        assert [item["path"] for item in query["entries"]] == ["/" + path for path in expected]
        assert [item["path"] for item in search["matches"]] == ["/" + path for path in expected]


@pytest.mark.anyio
async def test_search_paging_context_crlf_and_per_file_limit(tmp_path, search_files):
    (tmp_path / "a.txt").write_bytes(b"before\nMATCH-long\r\nafter\nMATCH\n")
    (tmp_path / "b.txt").write_text("MATCH\n")
    arguments = dict(pattern="match", case_sensitive=False, max_line_length=5, context_lines=1)
    whole = await search_files("search", **arguments)
    assert len(whole["matches"]) == 3
    pages = [await search_files("search", **arguments, offset=offset, max_matches=1) for offset in range(4)]
    assert [page["has_more"] for page in pages] == [True, True, False, False]
    assert [match for page in pages for match in page["matches"]] == whole["matches"]
    first = whole["matches"][0]
    assert first["text"] == "MATCH" and first["text_truncated"]
    assert first["context"] == "befor\nMATCH\nafter\n"
    assert first["context_start_line"] == 1
    limited = await search_files("search", **arguments, max_matches_per_file=1)
    assert [match["path"] for match in limited["matches"]] == ["/a.txt", "/b.txt"]
    crlf = await search_files("search", pattern="MATCH-long", max_line_length=100)
    assert crlf["matches"][0]["text"] == "MATCH-long\r"


@pytest.mark.anyio
async def test_search_literal_zero_binary_symlink_and_file_ceiling(tmp_path, search_files):
    (tmp_path / "a.txt").write_text("a.b\naxb\n")
    (tmp_path / "b.txt").write_text("a.b\n")
    (tmp_path / "binary.txt").write_bytes(b"\x00a.b")
    (tmp_path / "invalid.txt").write_bytes(b"\xffa.b")
    (tmp_path / "link.txt").symlink_to(tmp_path / "a.txt")
    literal = await search_files("search", pattern="a.b")
    regex = await search_files("search", pattern="a.b", regex=True)
    assert len(literal["matches"]) == 2
    assert len(regex["matches"]) == 3
    empty = await search_files("search", pattern="absent")
    assert empty["matches"] == [] and not empty["has_more"]
    with pytest.raises((EnvironmentError, OverflowError)) as error:
        await search_files("search", pattern="absent", max_files=1)
    if isinstance(error.value, EnvironmentError):
        assert error.value.code == "environment_too_large"
    assert not (await search_files("search", pattern="a.b", max_file_bytes=2))["matches"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "arguments,field,reason",
    [
        ({"pattern": "(", "regex": True}, "pattern", "invalid_regex"),
        ({"pattern": ""}, "pattern", "empty_pattern"),
        ({"pattern": "ok", "include": "{broken}"}, "include", "invalid_glob"),
    ],
)
async def test_provider_diagnostics(search_files, arguments, field, reason):
    with pytest.raises((PatternError, EnvironmentError)) as error:
        await search_files("search", **arguments)
    assert error.value.details["field"] == field
    assert error.value.details["reason"] == reason
    assert error.value.details["hint"]


@pytest.mark.anyio
@pytest.mark.parametrize("repository", [False, True])
async def test_hidden_nested_ignore_and_unignore(tmp_path, search_files, repository):
    if repository:
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("ignored/\n*.tmp\n")
    for path in ["ignored/a.py", "src/a.py", "src/b.tmp", "src/keep.tmp", ".hidden.py"]:
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("needle")
    (tmp_path / "src/.gitignore").write_text("!keep.tmp\n")
    result = await search_files("search", pattern="needle", ignore_mode="git")
    assert [match["path"] for match in result["matches"]] == ["/src/a.py", "/src/keep.tmp"]
    result = await search_files("search", pattern="needle", ignore_mode="git", include_hidden=True)
    assert [match["path"] for match in result["matches"]] == ["/.hidden.py", "/src/a.py", "/src/keep.tmp"]


def test_guest_embedded_source_executes_and_returns_diagnostics(tmp_path):
    configuration = E2BProviderConfiguration(root=str(tmp_path), python=sys.executable)
    commands = GuestCommands(None, configuration)
    command = commands.command(
        "files",
        {
            "configuration": configuration.model_dump(mode="json"),
            "action": "query",
            "arguments": {"path": "/", "pattern": "{broken}", "max_results": 1},
        },
    )
    result = subprocess.run(shlex.split(command), capture_output=True, text=True, check=True)
    value = json.loads(result.stdout)
    assert value["error"] == "environment_request_invalid"
    assert value["details"]["field"] == "pattern"


@pytest.mark.anyio
async def test_large_offset_does_not_consume_result_budget(tmp_path, search_files):
    (tmp_path / "many.txt").write_text("needle\n" * 300)
    result = await search_files("search", pattern="needle", offset=250, max_matches=5)
    assert [match["line"] for match in result["matches"]] == list(range(251, 256))
    assert result["has_more"]


@pytest.mark.anyio
async def test_bracket_classes_in_query_and_include_and_terminal_recursive(tmp_path, search_files):
    for path in ["file[1].py", "file1.py", "filea.py", "src"]:
        (tmp_path / path).write_text("needle")
    for pattern, expected in [
        ("file[[]*.{py,rs}", ["/file[1].py"]),
        ("file[^0-9].{py,rs}", ["/filea.py"]),
        ("{src,tests}/**", []),
    ]:
        query = await search_files("query", pattern=pattern)
        search = await search_files("search", pattern="needle", include=pattern)
        assert [item["path"] for item in query["entries"]] == expected
        assert [item["path"] for item in search["matches"]] == expected


def test_guest_queries_page_without_metadata_for_all_matches_and_batches_ignore(tmp_path, monkeypatch):
    from a13n_environment.e2b.guest import files

    for index in range(50):
        (tmp_path / f"file-{index:02}.py").write_text("needle")
    metadata_calls = []
    git_calls = []
    original_metadata, original_run = files.metadata, files.subprocess.run

    def metadata(*args):
        metadata_calls.append(args[1])
        return original_metadata(*args)

    def run(command, **kwargs):
        git_calls.append(command)
        return original_run(command, **kwargs)

    monkeypatch.setattr(files, "metadata", metadata)
    monkeypatch.setattr(files.subprocess, "run", run)
    result = files.query(
        tmp_path,
        tmp_path,
        {"pattern": "*.py", "max_results": 1, "ignore_mode": "git"},
        {"read_only": False, "max_query_entries": 100},
    )
    assert result["has_more"] and len(result["entries"]) == 1
    assert len(metadata_calls) == 2
    assert sum("check-ignore" in command for command in git_calls) == 1
    assert any("--stdin" in command for command in git_calls)


@pytest.mark.anyio
async def test_full_relative_path_order_and_page_lookahead(tmp_path, search_files):
    (tmp_path / "a").mkdir()
    (tmp_path / "a/x.txt").write_text("needle")
    (tmp_path / "a.txt").write_text("needle")
    (tmp_path / "a0.txt").write_text("needle")
    for action, key, arguments in [
        ("query", "entries", {"pattern": "*.txt", "max_results": 1}),
        ("search", "matches", {"pattern": "needle", "max_matches": 1}),
    ]:
        pages = [await search_files(action, **arguments, offset=index) for index in range(4)]
        assert [item["path"] for page in pages for item in page[key]] == ["/a.txt", "/a/x.txt", "/a0.txt"]
        assert [page["has_more"] for page in pages] == [True, True, False, False]
    query = await search_files("query", pattern="**/*")
    assert [item["path"] for item in query["entries"]] == ["/a", "/a.txt", "/a/x.txt", "/a0.txt"]
