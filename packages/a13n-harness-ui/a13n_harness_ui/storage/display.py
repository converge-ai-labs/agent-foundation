"""Durable display prefixes, independent of native execution snapshots."""

from __future__ import annotations

from collections.abc import Sequence
from itertools import groupby

from a13n_stream_protocol.display import Item
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
    boundary = _input_start(items, next(reversed(groups.values())))
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


def _join_display(segments: Sequence[DisplayHistory]) -> DisplayHistory:
    return segments[-1].model_copy(
        update={
            "items": tuple(item for segment in segments for item in segment.items),
            "completed": tuple(key for segment in segments for key in segment.completed),
        }
    )


async def read_display_windows(objects: ImmutableObjectStore, archive: DisplayArchive) -> list[DisplayHistory]:
    """Restore each seal as projected when its absolute offsets were recorded."""
    segments: list[DisplayHistory] = []
    reference = archive.prefix
    while reference is not None:
        if reference.object_kind is not ObjectKind.display_segment:
            raise ValueError("Display prefix must reference a display segment")
        segment = await objects.read_model(reference, DisplaySegment)
        segments.append(segment.display)
        reference = segment.previous
    segments.reverse()
    # Segments split at 256 items share the producer cut that sealed them.
    windows = [_join_display(list(group)) for _, group in groupby(segments, key=lambda d: (d.run_id, d.position))]
    return [*windows, archive.tail]


async def read_display(objects: ImmutableObjectStore, archive: DisplayArchive) -> DisplayHistory:
    return _join_display(await read_display_windows(objects, archive))


def _display_input_key(item: Item) -> str:
    imported = item.content.get("turn")
    if not isinstance(imported, dict):
        return str(item.content.get("input_group") or item.id)
    key = str(imported["turn_id"])
    if key.startswith("entry:") and imported.get("timestamp") is not None:
        return f"{key}@{imported['timestamp']}"
    return key


def _display_input_boundaries(display: DisplayHistory) -> dict[str, int]:
    """Match archived input-group identities and their authored source aliases."""
    keys: dict[str, int] = {}
    ambiguous: set[str] = set()
    groups: dict[str, int] = {}
    for index, item in enumerate(display.items):
        if not ordinary_input(item):
            continue
        group = str(item.content.get("input_group") or item.id)
        group_start = groups.setdefault(group, index)
        key = _display_input_key(item)
        metadata = item.content.get("metadata")
        source_id = metadata.get("source_id") if isinstance(metadata, dict) else None
        for alias in (key, source_id):
            if isinstance(alias, str) and keys.setdefault(alias, group_start) != group_start:
                ambiguous.add(alias)
    return {key: index for key, index in keys.items() if key not in ambiguous}


def _input_start(items: Sequence[Item], boundary: int) -> int:
    group = items[boundary].content.get("input_group")
    # The composer can emit hidden parts before its first visible part. Keep
    # the Run header with those parts, but never cross the prior Run's end.
    for index in range(boundary - 1, -1, -1):
        item = items[index]
        if item.content.get("type") == "RUN_STARTED":
            boundary = index
            break
        if (group is not None and item.content.get("input_group") == group) or (
            item.kind == "observation"
            and "entry" not in item.content
            and item.content.get("type") not in {"RUN_FINISHED", "RUN_ERROR"}
        ):
            boundary = index
            continue
        break
    return boundary


def _before_display_input(display: DisplayHistory, start: int) -> DisplayHistory:
    items = display.items[:start]
    ids = {item.id for item in items}
    return DisplayHistory(
        items=items,
        position=display.position,
        completed=tuple(key for key in display.completed if key in ids),
    )


async def crop_display_archive(
    objects: ImmutableObjectStore, archive: DisplayArchive, turn_key: str
) -> DisplayArchive | None:
    """Crop before a selected native input, reusing sealed turns on tail edits."""

    def crop(display: DisplayHistory, *, sealed: bool) -> DisplayHistory | None:
        boundary = _display_input_boundaries(display).get(turn_key)
        if boundary is None:
            return None
        start = _input_start(display.items, boundary)
        if sealed:
            # An authored alias can occur in an earlier sealed turn. Older
            # archives may also have sealed hidden parts before the Run input.
            first = display.items[start].content
            if _display_input_key(display.items[boundary]) != turn_key or (
                start == 0 and "entry" not in first and first.get("type") != "RUN_STARTED"
            ):
                return None
        return _before_display_input(display, start)

    tail = await to_thread.run_sync(lambda: crop(archive.tail, sealed=archive.prefix is not None))
    if tail is not None:
        return archive.model_copy(update={"tail": tail})
    if archive.prefix is None:
        return None
    display = await read_display(objects, archive)
    prefix = await to_thread.run_sync(lambda: crop(display, sealed=False))
    return None if prefix is None else DisplayArchive(tail=prefix)
