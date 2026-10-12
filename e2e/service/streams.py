"""Reading a session stream as a client does: SSE frames and the assistant text their deltas carry."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx2
from a13n_stream_protocol.display import DisplayFold

from .api import TIMEOUT, Workspace


@dataclass(frozen=True)
class Frame:
    """One frame of a session stream: `thread`, or a run's `items`, `delta` or `gap`."""

    event: str
    data: dict[str, Any]

    def of(self, run_id: str) -> bool:
        return self.event != "thread" and self.data["run_id"] == run_id


@asynccontextmanager
async def session_stream(api: Workspace, session_id: str) -> AsyncIterator[AsyncIterator[Frame]]:
    """The session's SSE stream, which every connection starts from a snapshot."""
    path = f"/api/v1/sessions/{session_id}/stream"
    async with api.client.stream("GET", path, timeout=TIMEOUT) as response:
        assert response.status_code == 200, await response.aread()
        yield _parse(response)


async def _parse(response: httpx2.Response) -> AsyncIterator[Frame]:
    event, data = "", ""
    async for line in response.aiter_lines():
        if line.startswith("event: "):
            event = line.removeprefix("event: ")
        elif line.startswith("data: "):
            data = line.removeprefix("data: ")
        elif line == "" and event:
            yield Frame(event, json.loads(data))
            event, data = "", ""


async def read_until(frames: AsyncIterator[Frame], done: Callable[[Frame], bool]) -> list[Frame]:
    """Frames up to and including the first one `done` accepts."""
    seen = []
    async with asyncio.timeout(TIMEOUT):
        async for frame in frames:
            seen.append(frame)
            if done(frame):
                return seen
    raise AssertionError(f"The stream ended before the expected frame; saw {[frame.event for frame in seen]}")


def assistant_text(frames: list[Frame], run_id: str) -> str:
    """The assistant text the deltas of `run_id` stream, in arrival order."""
    display = DisplayFold(run_id)
    for frame in frames:
        if frame.of(run_id) and frame.event == "delta":
            display.attempt = frame.data["attempt"]
            display.sequence = frame.data["sequence"] - 1
            display.fold([frame.data["event"]])
    return "".join(
        str(item.content.get("text", ""))
        for item in display.items.values()
        if item.kind == "text_message" and item.content.get("role") == "assistant"
    )


def sealed(run_id: str) -> Callable[[Frame], bool]:
    """The run's final items, which follow its seal."""
    return lambda frame: frame.of(run_id) and frame.event == "items" and frame.data["complete"]
