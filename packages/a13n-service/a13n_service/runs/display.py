"""Service-owned immutable display pages and mutable checkpoint tail."""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from a13n_stream_protocol.display import (
    FRAGMENTS,
    MAX_FIELD_CHARS,
    MAX_OBSERVATION_BYTES,
    DisplayContinuation,
    Item,
    ItemKind,
    ItemRef,
    ItemState,
    Observed,
    StreamPosition,
    extend,
    fragment,
    item_id,
    open_tool_calls,
)
from a13n_stream_protocol.display import (
    DisplayFold as SemanticDisplayFold,
)
from pydantic import BaseModel, ConfigDict, Field, JsonValue

# Re-export existing Service imports while keeping the semantic contract shared.
__all__ = [
    "FRAGMENTS",
    "MAX_FIELD_CHARS",
    "MAX_OBSERVATION_BYTES",
    "DisplayFold",
    "Item",
    "ItemKind",
    "ItemRef",
    "ItemState",
    "Observed",
    "Page",
    "Snapshot",
    "StreamPosition",
    "Tail",
    "extend",
    "fragment",
    "item_id",
    "open_tool_calls",
]


class Tail(BaseModel):
    """The items of a run's display not in a page at one checkpoint, and the stream position the display covers."""

    model_config = ConfigDict(extra="forbid")
    # The ordinal of the first item, or of the next one when there is none: every earlier item is in a page.
    first: int = Field(default=1, ge=1)
    items: list[Item] = Field(default_factory=list)
    position: StreamPosition = StreamPosition(attempt=0, sequence=0)
    # Optional Redis resume hint; attempts without confirmed writes have none.
    resume_after: str | None = None
    continuation: DisplayContinuation | None = None


class Page(BaseModel):
    """Consecutive final items of a run's display, written once."""

    model_config = ConfigDict(extra="forbid")
    items: list[Item]


@dataclass(frozen=True, slots=True)
class Snapshot:
    """What one checkpoint writes of the display: the pages it filled and the tail after them."""

    pages: list[Page]
    tail: Tail


def _size(item: Item) -> int:
    return len(item.model_dump_json().encode("utf-8"))


class DisplayFold(SemanticDisplayFold):
    """Add Service page selection and retirement to the shared semantic fold."""

    def __init__(self, run_id: str, tail: Tail, *, attempt: int, page_items: int, page_bytes: int):
        super().__init__(
            run_id,
            tail.items,
            attempt=attempt,
            first=tail.first,
            continuation=tail.continuation if tail.position.attempt == attempt else None,
        )
        self.first = tail.first
        self.page_items, self.page_bytes = page_items, page_bytes
        self.paged: set[str] = set()
        self.sizes: dict[str, int] = {}

    def interrupt(self, open_calls: Collection[str] = ()) -> None:
        """Host terminal/retry policy, separate from pure checkpoint capture."""
        kept = {item_id(self.run_id, "tool_call", call) for call in open_calls}
        for key, item in self.items.items():
            if item.state == "in_progress" and key not in kept:
                self.items[key] = item.model_copy(update={"state": "interrupted"})
                self.changed.add(key)

    def snapshot(self) -> Snapshot:
        """Freeze active continuation and page only the immutable prefix; do not close active blocks."""
        for key in self.changed & self.items.keys():
            self.sizes[key] = _size(self.items[key])
        self.changed.clear()
        frozen = self.export()
        items = frozen.items
        pages: list[Page] = []
        start, size = 0, 0
        for end, item in enumerate(items, 1):
            if item.state == "in_progress" or (
                self.arguments is not None
                and item.id == self.arguments.key
                and self.arguments.sequence == self.sequence
            ):
                break
            size += self.sizes[item.id]
            if end - start == self.page_items or size >= self.page_bytes:
                pages.append(Page(items=items[start:end]))
                start, size = end, 0
        return Snapshot(
            pages,
            Tail(
                first=self.first + start,
                items=items[start:],
                position=self.position,
                continuation=frozen.continuation,
            ),
        )

    def pending(self, snapshot: Snapshot) -> Snapshot:
        """Drop pages an earlier frozen boundary published while production advanced."""
        return Snapshot(
            pages=[page for page in snapshot.pages if page.items[-1].ordinal >= self.first],
            tail=snapshot.tail,
        )

    def committed(self, snapshot: Snapshot) -> None:
        """The snapshot's pages were committed: their items leave the tail for good."""
        for page in snapshot.pages:
            for item in page.items:
                del self.items[item.id], self.sizes[item.id]
                self.paged.add(item.id)
        self.first = snapshot.tail.first

    def _put(
        self, key: str, kind: ItemKind, state: ItemState, content: dict[str, JsonValue], *, at: datetime
    ) -> ItemRef:
        if key in self.paged:
            raise RuntimeError("An event changed a display item that is already in a page")
        return super()._put(key, kind, state, content, at=at)
