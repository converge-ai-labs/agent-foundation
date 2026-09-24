from __future__ import annotations

import json
from collections.abc import Awaitable
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_harness.capabilities import RECORD_TOOL_KEYS, RecordMount, RecordToolKey
from a13n_harness.context import AgentContext
from a13n_harness.providers.memory import MemoryStoreError
from a13n_harness.tools.metadata import (
    HARNESS_TOOL_METADATA_KEY,
    RECOVERY_RETRY_SAFE_METADATA_KEY,
    ToolOutputPolicy,
)
from a13n_harness.toolsets.memory_records import MemoryRecordToolset
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ToolFailed

from .memory_helpers import FakeRecordStore

pytestmark = pytest.mark.anyio


def _tools(
    *mounts: RecordMount, record_chars: int = 8000, tools: tuple[RecordToolKey, ...] = RECORD_TOOL_KEYS
) -> MemoryRecordToolset:
    return MemoryRecordToolset(mounts, record_chars=record_chars, tools=tools)


def _ctx() -> RunContext[AgentContext]:
    return cast(RunContext[AgentContext], SimpleNamespace(tool_call_id="call-1"))


async def _fails(call: Awaitable[object]) -> dict[str, Any]:
    with pytest.raises(ToolFailed) as caught:
        await call
    return json.loads(caught.value.message)


async def test_search_and_list_return_records_of_the_memory() -> None:
    store = FakeRecordStore(["likes tea", "works in Berlin", "prefers short answers"])
    tools = _tools(RecordMount("facts", store, "read"))

    assert await tools.search(_ctx(), "facts", "drinks", limit=2) == {
        "memory": "facts",
        "records": [
            {"id": "r1", "text": "likes tea", "score": 1.0},
            {"id": "r2", "text": "works in Berlin", "score": 1.0},
        ],
    }
    assert store.searches == [("drinks", 2)]
    first = await tools.list(_ctx(), "facts", limit=2)
    assert first == {
        "memory": "facts",
        "records": [{"id": "r1", "text": "likes tea"}, {"id": "r2", "text": "works in Berlin"}],
        "next_cursor": "2",
    }
    assert await tools.list(_ctx(), "facts", limit=2, cursor="2") == {
        "memory": "facts",
        "records": [{"id": "r3", "text": "prefers short answers"}],
        "next_cursor": None,
    }


async def test_writes_add_update_and_delete_records() -> None:
    store = FakeRecordStore(["likes tea"])
    tools = _tools(RecordMount("facts", store, "write"))

    assert await tools.add(_ctx(), "facts", "works in Berlin") == {"memory": "facts", "id": "r2"}
    assert await tools.update(_ctx(), "facts", "r1", "likes coffee") == {"memory": "facts", "id": "r1"}
    assert await tools.delete(_ctx(), "facts", "r2") == {"memory": "facts", "id": "r2", "deleted": True}
    assert store.records == {"r1": "likes coffee"}


@pytest.mark.parametrize("text", ["   ", "x" * 11])
async def test_a_record_is_not_blank_and_within_the_character_limit(text: str) -> None:
    store = FakeRecordStore(["likes tea"])
    tools = _tools(RecordMount("facts", store, "write"), record_chars=10)

    assert (await _fails(tools.add(_ctx(), "facts", text)))["error"] == "invalid_text"
    assert (await _fails(tools.update(_ctx(), "facts", "r1", text)))["error"] == "invalid_text"
    assert store.calls == []


async def test_store_refusals_reach_the_model_as_failures() -> None:
    store = FakeRecordStore(["likes tea"])
    tools = _tools(RecordMount("facts", store, "write"))

    failure = await _fails(tools.delete(_ctx(), "facts", "r9"))
    assert failure == {"error": "record_not_found", "message": "No record 'r9' in this memory."}

    async def unconfirmed(operation: str) -> None:
        raise MemoryStoreError("write_unconfirmed", "The backend did not confirm the write.")

    store.before = unconfirmed
    assert (await _fails(tools.add(_ctx(), "facts", "likes coffee")))["error"] == "write_unconfirmed"
    assert store.calls.count("add") == 1


async def test_mount_names_and_access_are_checked_at_the_call() -> None:
    tools = _tools(RecordMount("team", FakeRecordStore(["rule"]), "read"))
    assert (await _fails(tools.search(_ctx(), "user", "rule")))["error"] == "unknown_memory"
    assert (await _fails(tools.add(_ctx(), "team", "another rule")))["error"] == "forbidden"
    assert (await _fails(tools.delete(_ctx(), "team", "r1")))["error"] == "forbidden"


async def test_tools_are_offered_per_access_and_selection() -> None:
    facts = RecordMount("facts", FakeRecordStore(), "write")
    team = RecordMount("team", FakeRecordStore(), "read")

    both = _tools(facts, team).get_toolset()
    assert both is not None
    definitions = {name: await tool.prepare_tool_def(_ctx()) for name, tool in both.tools.items()}
    assert list(definitions) == [f"memory_record_{key}" for key in RECORD_TOOL_KEYS]
    for key in RECORD_TOOL_KEYS:
        definition = definitions[f"memory_record_{key}"]
        assert definition is not None
        expected = ["facts", "team"] if key in {"search", "list"} else ["facts"]
        assert definition.parameters_json_schema["properties"]["memory"]["enum"] == expected

    read_only = _tools(team).get_toolset()
    assert read_only is not None and list(read_only.tools) == ["memory_record_search", "memory_record_list"]
    selected = _tools(facts, tools=("search", "add")).get_toolset()
    assert selected is not None and list(selected.tools) == ["memory_record_search", "memory_record_add"]
    assert _tools(team, tools=("add",)).get_toolset() is None
    assert _tools().get_toolset() is None


async def test_tool_metadata_declares_effects_and_recovery() -> None:
    toolset = _tools(RecordMount("facts", FakeRecordStore(), "write")).get_toolset()
    assert toolset is not None
    effects = {"search": {"read"}, "list": {"read"}, "add": {"write"}, "update": {"write"}, "delete": {"delete"}}
    for key, expected in effects.items():
        tool = toolset.tools[f"memory_record_{key}"]
        metadata = (tool.metadata or {})[HARNESS_TOOL_METADATA_KEY]
        reads = key in {"search", "list"}
        assert metadata.tool_id == f"memory.record.{key}"
        assert metadata.effects == expected
        assert metadata.idempotency == ("read_only" if reads else "none")
        assert isinstance(metadata.output_policy, ToolOutputPolicy)
        assert metadata.output_policy.overflow == "truncate"
        assert (tool.metadata or {}).get(RECOVERY_RETRY_SAFE_METADATA_KEY, False) is reads
