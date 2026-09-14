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


async def parse_frames(lines: AsyncIterator[str], *, native: bool = False) -> AsyncIterator[Event]:
    """Read SSE framing without interpreting either protocol's cursor or JSON."""
    cursor, kind, data = "", "", []
    has_id = False
    size = 0
    async for line in lines:
        size += len(line.encode("utf-8")) + 1
        assert size <= 1024 * 1024, "Oversized SSE frame"
        if line == "":
            if data:
                if native:
                    assert len(data) == 1, "Native SSE requires one JSON data line"
                value = json.loads("\n".join(data), parse_constant=_invalid_json_constant)
                assert isinstance(value, dict), "SSE data must be a JSON object"
                yield Event(cursor, kind, value)
            elif native:
                assert not has_id, "Native heartbeat must not carry a replay cursor"
            cursor, kind, data, size = "", "", [], 0
            has_id = False
        elif not line.startswith(":"):
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "id":
                cursor = value
                has_id = True
            elif field == "event":
                kind = value
            elif field == "data":
                data.append(value)
    assert not data and not (native and (has_id or kind)), "Run Stream ended in the middle of an SSE frame"


def _invalid_json_constant(value):
    raise AssertionError(f"Non-JSON numeric constant in SSE: {value}")


async def parse_events(lines: AsyncIterator[str]) -> AsyncIterator[Event]:
    from .stream_contract import assert_native_envelope

    async for frame in parse_frames(lines, native=True):
        assert frame.kind != "a13n.service.replay_gap", "Run Stream reported a replay gap"
        assert frame.cursor and frame.kind, "Run Stream frame lacks an id or event type"
        stream_order(frame.cursor)
        assert frame.data["event_type"] == frame.kind
        assert_native_envelope(frame.data)
        yield frame


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
    terminal = [
        event for event in events if event.kind in {"run.completed", "run.cancelled", "run.failed", "run.waiting"}
    ]
    assert [event.kind for event in terminal] == [f"run.{outcome}"], "Unexpected durable Run terminal events"
