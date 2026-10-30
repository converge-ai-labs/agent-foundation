"""Durable display prefixes, independent of native execution snapshots."""

from __future__ import annotations

from anyio import to_thread
from pydantic import BaseModel, ConfigDict, Field

from a13n_harness_ui.display_history import DisplayHistory, ordinary_input

from .objects import ImmutableObjectStore, ObjectKind, ObjectRef


class DisplayArchive(BaseModel):
    """A sealed prefix plus the revisable final turn, selected with native state."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    prefix: ObjectRef | None = None
    entry_offset: int = Field(default=0, ge=0)
    turn_offset: int = Field(default=0, ge=0)
    tail: DisplayHistory


class DisplaySegment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    previous: ObjectRef | None = None
    display: DisplayHistory


def _split(archive: DisplayArchive) -> tuple[DisplayHistory, DisplayHistory, int, int] | None:
    from a13n_harness_ui.display_projection import display_entries, display_turns

    items = archive.tail.items
    # Keep every part of the final input group and its response in the revisable
    # suffix. A deferred/input-free resume therefore retains its existing row.
    groups: dict[str, int] = {}
    for index, item in enumerate(items):
        if ordinary_input(item):
            groups.setdefault(str(item.content.get("input_group") or item.id), index)
    if len(groups) < 2:
        return None
    boundary = next(reversed(groups.values()))
    # RUN_STARTED precedes the input it describes. Keep it with that input so
    # later failures retain their producing run attribution.
    for index in range(boundary - 1, -1, -1):
        if items[index].content.get("type") == "RUN_STARTED":
            boundary = index
            break
        if items[index].kind != "observation" or "entry" in items[index].content:
            break
    prefix_ids = {item.id for item in items[:boundary]}
    prefix = archive.tail.model_copy(
        update={
            "items": items[:boundary],
            "completed": tuple(key for key in archive.tail.completed if key in prefix_ids),
            "continuation": None,
            "pending_response": None,
        }
    )
    tail = archive.tail.model_copy(
        update={
            "items": items[boundary:],
            "completed": tuple(key for key in archive.tail.completed if key not in prefix_ids),
        }
    )
    return prefix, tail, len(display_entries(prefix)), len(display_turns(prefix))


async def seal_display(objects: ImmutableObjectStore, archive: DisplayArchive) -> DisplayArchive:
    """Publish reusable prefix chunks without changing any selected checkpoint.

    Called before a collector starts, never concurrently with its producer. A
    failed publication leaves the old head usable; orphan chunks are harmless.
    """
    split = await to_thread.run_sync(_split, archive)
    if split is None:
        return archive
    prefix, tail, entries, turns = split
    reference = archive.prefix
    completed = set(prefix.completed)
    for start in range(0, len(prefix.items), 256):
        items = prefix.items[start : start + 256]
        segment = DisplaySegment(
            previous=reference,
            display=prefix.model_copy(
                update={
                    "items": items,
                    "completed": tuple(item.id for item in items if item.id in completed),
                }
            ),
        )
        reference = (await objects.publish_model(object_kind=ObjectKind.display_segment, value=segment)).ref
    return DisplayArchive(
        prefix=reference,
        entry_offset=archive.entry_offset + entries,
        turn_offset=archive.turn_offset + turns,
        tail=tail,
    )


async def read_display(objects: ImmutableObjectStore, archive: DisplayArchive) -> DisplayHistory:
    """Materialize history only for inspection rebuilds and explicit originals."""
    segments: list[DisplayHistory] = []
    reference = archive.prefix
    while reference is not None:
        if reference.object_kind is not ObjectKind.display_segment:
            raise ValueError("Display prefix must reference a display segment")
        segment = await objects.read_model(reference, DisplaySegment)
        segments.append(segment.display)
        reference = segment.previous
    segments.reverse()
    segments.append(archive.tail)
    return archive.tail.model_copy(
        update={
            "items": tuple(item for segment in segments for item in segment.items),
            "completed": tuple(key for segment in segments for key in segment.completed),
        }
    )
