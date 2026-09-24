"""Model-facing record memory tools: similarity search, listing, and writes where the last writer wins."""

from __future__ import annotations

from collections.abc import Collection, Iterator, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, JsonValue
from pydantic_ai import RunContext

from a13n_harness.context import AgentContext
from a13n_harness.providers.memory import MemoryRecord, MemoryStoreError, validate_record_text

from ._instructions import InstructionFunctionToolset, tool_instruction
from ._memory import MemoryName, MemoryTool, failure, memory_toolset, mounted

if TYPE_CHECKING:
    from a13n_harness.capabilities.memory import RecordMount

type RecordToolKey = Literal["search", "list", "add", "update", "delete"]

_TOOLS: dict[RecordToolKey, MemoryTool] = {
    "search": MemoryTool(
        "read", frozenset({"read"}), 64 * 1024, "Find the records of a memory closest in meaning to a query."
    ),
    "list": MemoryTool("read", frozenset({"read"}), 64 * 1024, "List the records of a memory, one page at a time."),
    "add": MemoryTool("write", frozenset({"write"}), 4096, "Add one record to a memory."),
    "update": MemoryTool("write", frozenset({"write"}), 4096, "Replace the whole text of a record."),
    "delete": MemoryTool("write", frozenset({"delete"}), 4096, "Delete a record."),
}
RECORD_TOOL_KEYS: tuple[RecordToolKey, ...] = tuple(_TOOLS)
_INSTRUCTION = tool_instruction("memory-records")

_RecordId = Annotated[str, Field(min_length=1, max_length=256, description="The record's id")]
_Text = Annotated[str, Field(min_length=1, description="The record's whole text, one self-contained statement")]


class MemoryRecordToolset:
    """The record memory tools over one capability's mounts.

    A tool is offered when it is enabled and some mount's access allows it; its
    `memory` enum lists exactly those mounts. Writes are not versioned: the last
    writer wins, and a write the store does not confirm fails with
    `write_unconfirmed`.
    """

    def __init__(self, mounts: Sequence[RecordMount], *, record_chars: int, tools: Collection[RecordToolKey]) -> None:
        self._mounts = {mount.name: mount for mount in mounts}
        self._record_chars = record_chars
        self._enabled = frozenset(tools)

    def get_toolset(self) -> InstructionFunctionToolset | None:
        return memory_toolset(self, "record", _TOOLS, self._enabled, self._mounts, _INSTRUCTION)

    async def search(
        self,
        ctx: RunContext[AgentContext],
        memory: MemoryName,
        query: Annotated[str, Field(min_length=1, max_length=2000, description="What to look for, in words")],
        limit: Annotated[int, Field(ge=1, le=20, description="Maximum records to return")] = 5,
    ) -> dict[str, JsonValue]:
        del ctx
        mount = mounted(self._mounts, memory, "read")
        with _store_failures():
            records = await mount.store.search(query, limit=limit)
        return {"memory": mount.name, "records": [record_json(record) for record in records]}

    async def list(
        self,
        ctx: RunContext[AgentContext],
        memory: MemoryName,
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum records to return")] = 20,
        cursor: Annotated[str | None, Field(description="The next_cursor of the previous page")] = None,
    ) -> dict[str, JsonValue]:
        del ctx
        mount = mounted(self._mounts, memory, "read")
        with _store_failures():
            page = await mount.store.list(limit=limit, cursor=cursor)
        return {
            "memory": mount.name,
            "records": [record_json(record) for record in page.records],
            "next_cursor": page.next_cursor,
        }

    async def add(self, ctx: RunContext[AgentContext], memory: MemoryName, text: _Text) -> dict[str, JsonValue]:
        del ctx
        mount = mounted(self._mounts, memory, "write")
        with _store_failures():
            record = await mount.store.add(validate_record_text(text, max_chars=self._record_chars))
        return {"memory": mount.name, "id": record.id}

    async def update(
        self, ctx: RunContext[AgentContext], memory: MemoryName, id: _RecordId, text: _Text
    ) -> dict[str, JsonValue]:
        del ctx
        mount = mounted(self._mounts, memory, "write")
        with _store_failures():
            record = await mount.store.update(id, validate_record_text(text, max_chars=self._record_chars))
        return {"memory": mount.name, "id": record.id}

    async def delete(self, ctx: RunContext[AgentContext], memory: MemoryName, id: _RecordId) -> dict[str, JsonValue]:
        del ctx
        mount = mounted(self._mounts, memory, "write")
        with _store_failures():
            await mount.store.delete(id)
        return {"memory": mount.name, "id": id, "deleted": True}


def record_json(record: MemoryRecord) -> dict[str, JsonValue]:
    """A record as the model sees it, in tool results and recall."""
    value: dict[str, JsonValue] = {"id": record.id, "text": record.text}
    if record.score is not None:
        value["score"] = record.score
    if record.updated_at is not None:
        value["updated_at"] = record.updated_at.isoformat()
    return value


@contextmanager
def _store_failures() -> Iterator[None]:
    try:
        yield
    except MemoryStoreError as error:
        raise failure(error.code, str(error)) from None


__all__ = ["RECORD_TOOL_KEYS", "MemoryRecordToolset", "RecordToolKey", "record_json"]
