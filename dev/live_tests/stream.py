"""Strict parsing of the native Run Stream's SSE envelopes."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from itertools import pairwise


@dataclass(frozen=True)
class Event:
    cursor: str
    kind: str
    data: dict


async def parse_events(lines: AsyncIterator[str]) -> AsyncIterator[Event]:
    cursor, kind, data = "", "", []
    size = 0
    async for line in lines:
        size += len(line)
        assert size <= 1024 * 1024, "Oversized SSE frame"
        if line == "":
            if data:
                value = json.loads("\n".join(data))
                assert kind != "run_stream.replay_gap", "Run Stream reported a replay gap"
                assert cursor and kind, f"Run Stream frame lacks an id or event type: {kind}"
                stream_order(cursor)
                assert value["event_type"] == kind
                yield Event(cursor, kind, value)
            cursor, kind, data, size = "", "", [], 0
        elif not line.startswith(":"):
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "id":
                cursor = value
            elif field == "event":
                kind = value
            elif field == "data":
                data.append(value)
    assert not data, "Run Stream ended in the middle of an SSE frame"


def stream_order(cursor: str) -> tuple[int, int]:
    left, separator, right = cursor.partition("-")
    assert separator and left.isdigit() and right.isdigit(), f"Invalid stream cursor: {cursor}"
    return int(left), int(right)


def assert_stream(events: list[Event], run_id: str, outcome: str = "completed") -> None:
    assert events, f"No events for {run_id}"
    cursors = [stream_order(event.cursor) for event in events]
    assert all(first < second for first, second in pairwise(cursors))
    identities = [event.data["event_id"] for event in events]
    assert len(set(identities)) == len(identities), "Duplicate durable event identity"
    assert all(event.data["run_id"] == run_id for event in events)
    terminal = [event for event in events if event.kind in {"run.completed", "run.cancelled", "run.failed"}]
    assert [event.kind for event in terminal] == [f"run.{outcome}"], "Unexpected durable Run terminal events"
