"""Reading a thread stream as a client does: SSE frames and the assistant text their deltas carry."""

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
    """One frame of a thread stream: `delta` and `boundary` carry an ID; `changed`, `reset` and `gap` do not."""

    event: str
    id: str | None
    data: dict[str, Any]

    def of(self, run_id: str) -> bool:
        return self.event in {"delta", "boundary", "reset", "gap"} and self.data["run_id"] == run_id

    @property
    def kind(self) -> str | None:
        """The raw AG-UI event type, if this frame carries a delta."""
        return self.data["event"]["type"] if self.event == "delta" else None


@asynccontextmanager
async def thread_stream(
    api: Workspace, thread_id: str, *, last_event_id: str | None = None
) -> AsyncIterator[AsyncIterator[Frame]]:
    """The thread's SSE stream from `last_event_id`, as a client that reconnects would open it."""
    headers = {"last-event-id": last_event_id} if last_event_id is not None else {}
    path = f"/api/v1/threads/{thread_id}/stream"
    async with api.client.stream("GET", path, headers=headers, timeout=TIMEOUT) as response:
        assert response.status_code == 200, await response.aread()
        yield _parse(response)


async def _parse(response: httpx2.Response) -> AsyncIterator[Frame]:
    event, frame_id, data = "", None, ""
    async for line in response.aiter_lines():
        if line.startswith("event: "):
            event = line.removeprefix("event: ")
        elif line.startswith("id: "):
            frame_id = line.removeprefix("id: ")
        elif line.startswith("data: "):
            data = line.removeprefix("data: ")
        elif line == "" and event:
            yield Frame(event, frame_id, json.loads(data))
            event, frame_id, data = "", None, ""


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


def finished(run_id: str) -> Callable[[Frame], bool]:
    return lambda frame: frame.of(run_id) and frame.kind == "RUN_FINISHED"
