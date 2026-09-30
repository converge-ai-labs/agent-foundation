"""Compact display values and atomic, revision-checked display operations.

Execution interpretation belongs to the projector. This module is deliberately
transport independent; its applicator is also the reference for browser clients.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class DisplayValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_serialization_defaults_required=True)


class Producer(DisplayValue):
    run_id: str
    generation: str


class DisplayPosition(DisplayValue):
    producer: Producer
    sequence: int = Field(default=0, ge=0)


class DisplayScope(DisplayValue):
    id: str
    thread_id: str
    run_id: str
    parent_scope_id: str | None = None
    parent_tool_call_id: str | None = None
    invocation_id: str | None = None
    status: Literal["running", "completed", "failed", "cancelled", "deferred"] = "running"


type BlockKind = Literal["input", "text", "reasoning", "tool_chunk", "context_summary", "media", "extension"]
type BlockStatus = Literal["pending", "running", "succeeded", "failed", "cancelled", "deferred", "unknown"]


class DisplayBlock(DisplayValue):
    id: str
    scope_id: str
    kind: BlockKind
    revision: int = Field(ge=1)
    status: BlockStatus = "pending"
    content: dict[str, JsonValue] = Field(default_factory=dict)
    # Native addresses survive context replacement for inspection and comments.
    message_index: int | None = Field(default=None, ge=0)
    part_index: int | None = Field(default=None, ge=0)


class BlockPut(DisplayValue):
    op: Literal["block.put"] = "block.put"
    block: DisplayBlock
    expected_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def sequential_revision(self) -> Self:
        if self.block.revision != self.expected_revision + 1:
            raise ValueError("block.put must advance exactly one revision")
        return self


class BlockAppend(DisplayValue):
    op: Literal["block.append"] = "block.append"
    id: str
    field: Literal["text", "arguments", "signature"]
    expected_revision: int = Field(ge=1)
    revision: int = Field(ge=2)
    value: str

    @model_validator(mode="after")
    def sequential_revision(self) -> Self:
        if self.revision != self.expected_revision + 1:
            raise ValueError("block.append must advance exactly one revision")
        return self


class ScopePut(DisplayValue):
    op: Literal["scope.put"] = "scope.put"
    scope: DisplayScope


class BlocksRemove(DisplayValue):
    op: Literal["blocks.remove"] = "blocks.remove"
    ids: tuple[str, ...]
    omitted: int = Field(ge=0)


type DisplayOperation = Annotated[BlockPut | BlockAppend | ScopePut | BlocksRemove, Field(discriminator="op")]


class DisplayDelta(DisplayValue):
    format: Literal["display-delta/1"] = "display-delta/1"
    producer: Producer
    from_sequence: int = Field(ge=0)
    through_sequence: int = Field(ge=1)
    operations: tuple[DisplayOperation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def atomic_sequence(self) -> Self:
        if self.through_sequence != self.from_sequence + 1:
            raise ValueError("A display batch advances exactly one sequence")
        return self


class DisplaySnapshot(DisplayValue):
    format: Literal["display/1"] = "display/1"
    position: DisplayPosition
    scopes: tuple[DisplayScope, ...] = ()
    blocks: tuple[DisplayBlock, ...] = ()
    omitted: int = Field(default=0, ge=0)
    continuity: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_addresses(self) -> Self:
        scopes = {scope.id for scope in self.scopes}
        if len(scopes) != len(self.scopes) or len({block.id for block in self.blocks}) != len(self.blocks):
            raise ValueError("Display identities must be unique")
        if any(block.scope_id not in scopes for block in self.blocks):
            raise ValueError("Display block refers to an absent scope")
        if any(scope.parent_scope_id is not None and scope.parent_scope_id not in scopes for scope in self.scopes):
            raise ValueError("Display scope refers to an absent parent")
        parents = {scope.id: scope.parent_scope_id for scope in self.scopes}
        for scope in self.scopes:
            visited: set[str] = set()
            current: str | None = scope.id
            while current is not None:
                if current in visited:
                    raise ValueError("Display scope lineage must be acyclic")
                visited.add(current)
                current = parents[current]
        return self


class DisplayGap(ValueError):
    """An uncovered batch cannot apply; acquire an authoritative baseline."""


class DisplayState:
    """Serial-call applicator. A rejected batch never changes visible state.

    Only changed values are staged, not a copy of the whole transcript per token.
    Captures are detached; callers cannot mutate a published checkpoint through
    Pydantic's shallow frozen dictionaries.
    """

    def __init__(self, snapshot: DisplaySnapshot) -> None:
        self.restore(snapshot)

    def restore(self, snapshot: DisplaySnapshot) -> None:
        snapshot = snapshot.model_copy(deep=True)
        self.position = snapshot.position
        self.blocks = {block.id: block for block in snapshot.blocks}
        self.scopes = {scope.id: scope for scope in snapshot.scopes}
        self.omitted = snapshot.omitted
        self.continuity = snapshot.continuity

    def capture(self) -> DisplaySnapshot:
        return DisplaySnapshot(
            position=self.position,
            blocks=tuple(self.blocks.values()),
            scopes=tuple(self.scopes.values()),
            omitted=self.omitted,
            continuity=self.continuity,
        ).model_copy(deep=True)

    def publish(self, operations: Iterable[DisplayOperation]) -> DisplayDelta | None:
        """Allocate a dense sequence only after a complete batch is assembled."""
        operations = tuple(operations)
        if not operations:
            return None
        delta = DisplayDelta(
            producer=self.position.producer,
            from_sequence=self.position.sequence,
            through_sequence=self.position.sequence + 1,
            operations=operations,
        )
        self.apply(delta)
        return delta

    def apply(self, delta: DisplayDelta) -> bool:
        if delta.producer != self.position.producer:
            raise DisplayGap("Display producer changed")
        if delta.through_sequence <= self.position.sequence:
            return False
        if delta.from_sequence != self.position.sequence:
            raise DisplayGap("Display sequence is not contiguous")
        self.stage(delta.operations)
        self.position = DisplayPosition(producer=delta.producer, sequence=delta.through_sequence)
        return True

    def stage(self, operations: Iterable[DisplayOperation]) -> None:
        """Apply producer-local operations atomically, before assigning a batch sequence."""
        changed: dict[str, DisplayBlock | None] = {}
        scopes: dict[str, DisplayScope] = {}
        omitted = self.omitted
        for operation in operations:
            if isinstance(operation, ScopePut):
                scope = operation.scope
                parent = scope.parent_scope_id
                if parent is not None and parent not in scopes and parent not in self.scopes:
                    raise DisplayGap("Display scope parent is absent")
                existing = scopes.get(scope.id, self.scopes.get(scope.id))
                if existing is not None and existing.model_copy(update={"status": scope.status}) != scope:
                    raise DisplayGap("Display scope identity changed")
                if parent == scope.id:
                    raise DisplayGap("Display scope cannot parent itself")
                scopes[scope.id] = scope.model_copy(deep=True)
            elif isinstance(operation, BlocksRemove):
                for identifier in operation.ids:
                    if changed.get(identifier, self.blocks.get(identifier)) is None:
                        raise DisplayGap("Removed display block is absent")
                    changed[identifier] = None
                if operation.omitted < omitted:
                    raise DisplayGap("Display omission count moved backwards")
                omitted = operation.omitted
            else:
                identifier = operation.block.id if isinstance(operation, BlockPut) else operation.id
                previous = changed.get(identifier, self.blocks.get(identifier))
                revision = previous.revision if previous is not None else 0
                if revision != operation.expected_revision:
                    raise DisplayGap("Display block revision does not match")
                if isinstance(operation, BlockPut):
                    block = operation.block
                    if block.scope_id not in scopes and block.scope_id not in self.scopes:
                        raise DisplayGap("Display block scope is absent")
                    if previous is not None and (previous.scope_id, previous.kind) != (block.scope_id, block.kind):
                        raise DisplayGap("Display block identity changed")
                    changed[identifier] = block.model_copy(deep=True)
                else:
                    assert previous is not None
                    value = previous.content.get(operation.field)
                    if not isinstance(value, str):
                        raise DisplayGap("Display append field is not text")
                    changed[identifier] = previous.model_copy(
                        update={
                            "revision": operation.revision,
                            "content": {**previous.content, operation.field: value + operation.value},
                        }
                    )
        self.scopes.update(scopes)
        for identifier, block in changed.items():
            if block is None:
                self.blocks.pop(identifier, None)
            else:
                self.blocks[identifier] = block
        self.omitted = omitted
