from __future__ import annotations

import json
from collections.abc import Awaitable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_harness.capabilities import FileMount
from a13n_harness.context import AgentContext
from a13n_harness.providers.memory import DirectoryFileStore, FileFormat, Origin
from a13n_harness.tools.metadata import (
    HARNESS_TOOL_METADATA_KEY,
    RECOVERY_RETRY_SAFE_METADATA_KEY,
    ToolOutputPolicy,
)
from a13n_harness.toolsets.memory_files import FILE_TOOL_KEYS, FileToolKey, MemoryFileToolset
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ToolFailed

from .memory_helpers import FakeFileStore, SearchingFileStore

pytestmark = pytest.mark.anyio

ORIGIN = Origin(run_id="run-1", principal_id="user-1")


def _tools(
    *mounts: FileMount,
    retries: int = 3,
    tools: tuple[FileToolKey, ...] = FILE_TOOL_KEYS,
    format: FileFormat | None = None,
) -> MemoryFileToolset:
    return MemoryFileToolset(mounts, format=format or FileFormat(), write_retries=retries, origin=ORIGIN, tools=tools)


def _ctx(call: str = "call-1") -> RunContext[AgentContext]:
    return cast(RunContext[AgentContext], SimpleNamespace(tool_call_id=call))


async def _fails(call: Awaitable[object]) -> dict[str, Any]:
    with pytest.raises(ToolFailed) as caught:
        await call
    return json.loads(caught.value.message)


def _user(store: FakeFileStore | DirectoryFileStore) -> MemoryFileToolset:
    return _tools(FileMount("user", store, "write"))


async def test_view_lists_a_directory_level_and_reads_files() -> None:
    store = FakeFileStore({"README.md": "# Team\n", "prefs/tea.md": "likes tea\n", "prefs/deep/x.md": "x\n"})
    tools = _user(store)

    assert await tools.view(_ctx(), "user") == {
        "memory": "user",
        "path": "",
        "entries": [
            {"path": "README.md", "description": "# Team", "size": 7, "version": "v1"},
            {"path": "prefs/", "files": 2},
        ],
    }
    listed = await tools.view(_ctx(), "user", "prefs/")
    assert listed["entries"] == [
        {"path": "prefs/deep/", "files": 1},
        {"path": "prefs/tea.md", "description": "likes tea", "size": 10, "version": "v2"},
    ]
    assert await tools.view(_ctx(), "user", "prefs") == listed
    assert await tools.view(_ctx(), "user", "prefs/tea.md") == {
        "memory": "user",
        "path": "prefs/tea.md",
        "version": "v2",
        "size": 10,
        "content": "likes tea\n",
    }


@pytest.mark.parametrize(
    ("path", "code"), [("absent.md", "not_found"), ("absent/", "not_found"), ("../x", "invalid_path")]
)
async def test_view_reports_missing_and_invalid_paths(path: str, code: str) -> None:
    failure = await _fails(_user(FakeFileStore({"a.md": "a\n"})).view(_ctx(), "user", path))
    assert failure["error"] == code
    assert "current" not in failure


async def test_view_cuts_a_file_larger_than_the_current_limit() -> None:
    tools = _tools(
        FileMount("user", FakeFileStore({"big.md": "abcdefgh"}), "read"), format=FileFormat(max_file_bytes=4)
    )
    viewed = await tools.view(_ctx(), "user", "big.md")
    assert viewed["content"] == "abcd"
    assert viewed["size"] == 8
    assert viewed["truncated"] is True


async def test_grep_is_literal_and_case_insensitive_by_default() -> None:
    store = FakeFileStore({"a.md": "call F(X).Y here\nfxxy\n", "sub/b.md": "f(x).y again\n"})
    tools = _user(store)

    assert await tools.grep(_ctx(), "user", "f(x).y") == {
        "memory": "user",
        "matches": [
            {"path": "a.md", "line": 1, "text": "call F(X).Y here"},
            {"path": "sub/b.md", "line": 1, "text": "f(x).y again"},
        ],
        "truncated": False,
    }
    sensitive = await tools.grep(_ctx(), "user", "f(x).y", case_sensitive=True)
    assert [match["path"] for match in sensitive["matches"]] == ["sub/b.md"]
    regex = await tools.grep(_ctx(), "user", r"^f.x", regex=True)
    assert [(match["path"], match["line"]) for match in regex["matches"]] == [("a.md", 2), ("sub/b.md", 1)]
    scoped = await tools.grep(_ctx(), "user", "f(x)", path="sub/")
    assert [match["path"] for match in scoped["matches"]] == ["sub/b.md"]
    limited = await tools.grep(_ctx(), "user", "f", limit=2)
    assert len(limited["matches"]) == 2
    assert limited["truncated"] is True


async def test_grep_without_a_match_suggests_the_next_step() -> None:
    result = await _user(FakeFileStore({"a.md": "tea\n"})).grep(_ctx(), "user", "coffee")
    assert result["matches"] == []
    assert "memory_file_view" in result["hint"]


async def test_grep_rejects_an_invalid_regular_expression() -> None:
    failure = await _fails(_user(FakeFileStore({"a.md": "tea\n"})).grep(_ctx(), "user", "(", regex=True))
    assert failure["error"] == "invalid_pattern"


async def test_grep_uses_the_store_search_when_the_store_has_one() -> None:
    store = SearchingFileStore({"a.md": "tea\n"})
    result = await _user(store).grep(_ctx(), "user", "tea", path="notes/", regex=True, case_sensitive=True, limit=5)
    assert store.searches == [("tea", True, True, "notes/", 5)]
    assert result == {
        "memory": "user",
        "matches": [{"path": "native.md", "line": 3, "text": "from the store"}],
        "truncated": True,
    }
    assert "read" not in store.calls


async def test_create_writes_a_new_file_attributed_to_the_tool_call() -> None:
    store = FakeFileStore()
    assert await _user(store).create(_ctx("call-7"), "user", "prefs/tea.md", "likes tea\n") == {
        "memory": "user",
        "path": "prefs/tea.md",
        "version": "v1",
        "size": 10,
    }
    assert store.origins == [Origin(run_id="run-1", principal_id="user-1", tool_call_id="call-7")]


async def test_create_refuses_an_existing_path_and_returns_it() -> None:
    failure = await _fails(_user(FakeFileStore({"tea.md": "likes tea\n"})).create(_ctx(), "user", "tea.md", "x"))
    assert failure["error"] == "already_exists"
    assert failure["current"] == {"path": "tea.md", "version": "v1", "size": 10, "content": "likes tea\n"}


@pytest.mark.parametrize(
    ("content", "code"),
    [("---\ndescription: [unclosed\n---\n", "invalid_file"), ("x" * 65537, "too_large")],
)
async def test_create_refuses_invalid_files(content: str, code: str) -> None:
    store = FakeFileStore()
    failure = await _fails(_user(store).create(_ctx(), "user", "a.md", content))
    assert failure["error"] == code
    assert store.files == {}


async def test_edits_from_two_conversations_keep_each_others_changes() -> None:
    store = FakeFileStore({"prefs.md": "reply in English\nlikes tea\n"})
    a, b = _user(store), _user(store)
    assert (await a.view(_ctx(), "user", "prefs.md"))["version"] == "v1"

    assert (await b.edit(_ctx(), "user", "prefs.md", "reply in English", "reply in Chinese"))["version"] == "v2"
    assert (await a.edit(_ctx(), "user", "prefs.md", "likes tea", "likes coffee"))["version"] == "v3"
    assert store.files["prefs.md"].text == "reply in Chinese\nlikes coffee\n"

    failure = await _fails(a.edit(_ctx(), "user", "prefs.md", "reply in English", "reply in French"))
    assert failure["error"] == "no_match"
    assert failure["current"] == {
        "path": "prefs.md",
        "version": "v3",
        "size": 30,
        "content": "reply in Chinese\nlikes coffee\n",
    }
    assert store.files["prefs.md"].version == "v3"


async def test_edit_needs_exactly_one_occurrence_of_an_existing_file() -> None:
    tools = _user(FakeFileStore({"a.md": "tea\ntea\n"}))
    ambiguous = await _fails(tools.edit(_ctx(), "user", "a.md", "tea", "coffee"))
    assert ambiguous["error"] == "ambiguous_match"
    assert ambiguous["current"]["content"] == "tea\ntea\n"
    missing = await _fails(tools.edit(_ctx(), "user", "b.md", "tea", "coffee"))
    assert missing == {"error": "not_found", "message": missing["message"], "current": None}


@pytest.mark.parametrize(("text", "appended"), [("a", "a\nb\n"), ("a\n", "a\nb\n"), ("", "b\n")])
async def test_append_starts_on_a_new_line(text: str, appended: str) -> None:
    store = FakeFileStore({"log.md": text})
    await _user(store).append(_ctx(), "user", "log.md", "b\n")
    assert store.files["log.md"].text == appended


async def test_append_needs_an_existing_file() -> None:
    failure = await _fails(_user(FakeFileStore()).append(_ctx(), "user", "log.md", "b\n"))
    assert failure["error"] == "not_found"
    assert failure["current"] is None


async def test_move_renames_to_a_free_destination() -> None:
    store = FakeFileStore({"inbox/tea.md": "likes tea\n", "prefs/coffee.md": "likes coffee\n"})
    tools = _user(store)
    assert await tools.move(_ctx(), "user", "inbox/tea.md", "prefs/tea.md") == {
        "memory": "user",
        "path": "prefs/tea.md",
        "version": "v4",
    }
    taken = await _fails(tools.move(_ctx(), "user", "prefs/tea.md", "prefs/coffee.md"))
    assert taken["error"] == "already_exists"
    assert taken["current"]["path"] == "prefs/coffee.md"
    missing = await _fails(tools.move(_ctx(), "user", "inbox/tea.md", "x.md"))
    assert missing["error"] == "not_found"
    assert missing["current"] is None


async def test_delete_needs_the_viewed_version() -> None:
    store = FakeFileStore({"tea.md": "likes tea\n"})
    tools = _user(store)
    stale = await _fails(tools.delete(_ctx(), "user", "tea.md", "v0"))
    assert stale["error"] == "version_mismatch"
    assert stale["current"]["version"] == "v1"

    assert await tools.delete(_ctx(), "user", "tea.md", "v1") == {"memory": "user", "path": "tea.md", "deleted": True}
    missing = await _fails(tools.delete(_ctx(), "user", "tea.md", "v1"))
    assert missing["error"] == "not_found"
    assert missing["current"] is None


async def test_a_lost_compare_and_swap_rechecks_against_the_new_content() -> None:
    store = FakeFileStore({"prefs.md": "reply in English\nlikes tea\n"})

    def other_writer(operation: str) -> None:
        if operation == "write" and store.files["prefs.md"].version == "v1":
            store.put("prefs.md", "reply in Chinese\nlikes tea\n")

    store.before = other_writer
    await _user(store).edit(_ctx(), "user", "prefs.md", "likes tea", "likes coffee")
    assert store.files["prefs.md"].text == "reply in Chinese\nlikes coffee\n"
    assert store.calls.count("write") == 2


@pytest.mark.parametrize("key", ["edit", "move"])
async def test_writes_give_up_after_the_configured_retries(key: str) -> None:
    store = FakeFileStore({"a.md": "tea\n"})
    operation = "move" if key == "move" else "write"

    def other_writer(called: str) -> None:
        if called == operation:
            store.put("a.md", store.files["a.md"].text)

    store.before = other_writer
    tools = _tools(FileMount("user", store, "write"), retries=2)
    call = (
        tools.move(_ctx(), "user", "a.md", "b.md")
        if key == "move"
        else tools.edit(_ctx(), "user", "a.md", "tea", "coffee")
    )
    failure = await _fails(call)
    assert failure["error"] == "conflict_retries_exhausted"
    assert failure["current"]["version"] == store.files["a.md"].version
    assert store.calls.count(operation) == 3


async def test_store_refusals_reach_the_model_as_failures(tmp_path: Path) -> None:
    tools = _user(DirectoryFileStore(tmp_path))
    failure = await _fails(tools.create(_ctx(), "user", ".a13n-memory/x.md", "x"))
    assert failure["error"] == "invalid_path"


async def test_mount_names_and_access_are_checked_at_the_call() -> None:
    tools = _tools(FileMount("team", FakeFileStore({"a.md": "a\n"}), "read"))
    assert (await _fails(tools.view(_ctx(), "user", "")))["error"] == "unknown_memory"
    assert (await _fails(tools.create(_ctx(), "team", "b.md", "b")))["error"] == "forbidden"


async def test_tools_are_offered_per_access_and_selection() -> None:
    user = FileMount("user", FakeFileStore(), "write")
    team = FileMount("team", FakeFileStore(), "read")

    both = _tools(user, team).get_toolset()
    assert both is not None
    definitions = {name: await tool.prepare_tool_def(_ctx()) for name, tool in both.tools.items()}
    assert list(definitions) == [f"memory_file_{key}" for key in FILE_TOOL_KEYS]
    for key in FILE_TOOL_KEYS:
        definition = definitions[f"memory_file_{key}"]
        assert definition is not None
        expected = ["user", "team"] if key in {"view", "grep"} else ["user"]
        assert definition.parameters_json_schema["properties"]["memory"]["enum"] == expected

    read_only = _tools(team).get_toolset()
    assert read_only is not None and list(read_only.tools) == ["memory_file_view", "memory_file_grep"]
    selected = _tools(user, tools=("view", "edit")).get_toolset()
    assert selected is not None and list(selected.tools) == ["memory_file_view", "memory_file_edit"]
    assert _tools(team, tools=("create",)).get_toolset() is None
    assert _tools().get_toolset() is None


async def test_tool_metadata_declares_effects_and_recovery() -> None:
    toolset = _tools(FileMount("user", FakeFileStore(), "write")).get_toolset()
    assert toolset is not None
    effects = {
        "view": {"read"},
        "grep": {"read"},
        "create": {"write"},
        "edit": {"read", "write"},
        "append": {"read", "write"},
        "move": {"read", "write", "delete"},
        "delete": {"delete"},
    }
    for key, expected in effects.items():
        tool = toolset.tools[f"memory_file_{key}"]
        metadata = (tool.metadata or {})[HARNESS_TOOL_METADATA_KEY]
        reads = key in {"view", "grep"}
        assert metadata.tool_id == f"memory.file.{key}"
        assert metadata.effects == expected
        assert metadata.idempotency == ("read_only" if reads else "none")
        assert metadata.output_policy.overflow == "truncate"
        assert isinstance(metadata.output_policy, ToolOutputPolicy)
        assert (tool.metadata or {}).get(RECOVERY_RETRY_SAFE_METADATA_KEY, False) is reads
