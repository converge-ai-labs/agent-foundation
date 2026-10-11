"""A controllable OpenAI-compatible model process: each request is answered by a turn a test scripted for it.

Tests drive it over `/fixture/*`. A request takes the first scripted turn it matches: a turn scripted `to` a
marker matches only requests whose body contains that marker, so concurrent agents each get their own turns,
and a `repeat` turn answers every matching request instead of being used up. A turn that `hold`s a gate answers
only after the test opens it; a request whose client disconnects first is recorded as abandoned. Every request
body is kept with what became of it, so tests can assert exactly what the model was asked.
"""

import argparse
import asyncio
import json
import time
from collections.abc import Callable
from itertools import count
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .process import add_listener_arguments, serve_fixture

# How long a request waits for a matching turn before the fixture answers with an error.
TURN_WAIT_SECONDS = 30
POLL_SECONDS = 0.05


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Turn(BaseModel):
    to: str | None = None
    hold: str | None = None
    repeat: bool = False
    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    # The text streams as this many deltas, `delay` seconds apart.
    chunks: int = Field(default=1, ge=1)
    delay: float = Field(default=0, ge=0)


class Observed(BaseModel):
    index: int
    body: dict[str, Any]
    status: Literal["waiting", "held", "answered", "abandoned", "unscripted"] = "waiting"
    turn: int | None = None


app = FastAPI()
turns: dict[int, Turn] = {}
observed: list[Observed] = []
gates: dict[str, asyncio.Event] = {}
serial = count(1)


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/fixture/turns")
async def script(turn: Turn) -> dict[str, int]:
    turn_id = next(serial)
    turns[turn_id] = turn
    return {"id": turn_id}


@app.post("/fixture/gates/{name}")
async def open_gate(name: str) -> dict[str, str]:
    gates.setdefault(name, asyncio.Event()).set()
    return {"gate": name}


@app.get("/fixture/requests")
async def requests(marker: str | None = None) -> dict[str, list[Observed]]:
    return {"items": [item for item in observed if marker is None or marker in json.dumps(item.body)]}


@app.post("/v1/chat/completions")
async def complete(request: Request):
    body = await request.json()
    record = Observed(index=len(observed), body=body)
    observed.append(record)
    text = json.dumps(body)
    turn_id = await _wait(request, lambda: _match(text), timeout=TURN_WAIT_SECONDS)
    if turn_id is None:
        record.status = "abandoned" if await request.is_disconnected() else "unscripted"
        return JSONResponse({"error": {"message": "No scripted turn matched", "type": "server_error"}}, 503)
    turn = turns[turn_id] if turns[turn_id].repeat else turns.pop(turn_id)
    record.turn = turn_id
    if turn.hold is not None:
        record.status = "held"
        gate = gates.setdefault(turn.hold, asyncio.Event())
        if not await _wait(request, gate.is_set, timeout=None):
            record.status = "abandoned"
            return JSONResponse({"error": {"message": "The client left", "type": "server_error"}}, 503)
    record.status = "answered"
    if not body.get("stream"):
        return _message(turn)
    usage = bool(body.get("stream_options", {}).get("include_usage"))
    return StreamingResponse(_stream(turn, include_usage=usage), media_type="text/event-stream")


def _match(body: str) -> int | None:
    return next((key for key, turn in turns.items() if turn.to is None or turn.to in body), None)


async def _wait[T](request: Request, ready: Callable[[], T], *, timeout: float | None) -> T | None:
    """Poll `ready()` until it is truthy; None when the client disconnects or the timeout passes.

    Disconnection is checked first, so a request whose client is gone never takes a turn meant for its retry.
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    while not await request.is_disconnected():
        if value := ready():
            return value
        if deadline is not None and time.monotonic() > deadline:
            return None
        await asyncio.sleep(POLL_SECONDS)
    return None


USAGE = {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}


def _chunk(delta: dict[str, Any] | None, finish: str | None = None, *, usage: bool = False) -> str:
    choices = [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]
    chunk: dict[str, Any] = {
        "id": "chatcmpl-scripted",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "scripted",
        "choices": choices,
    }
    if usage:
        chunk["usage"] = USAGE
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


def _calls(turn: Turn) -> list[dict[str, Any]]:
    return [
        {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": json.dumps(call.arguments)}}
        for call in turn.tool_calls
    ]


async def _stream(turn: Turn, *, include_usage: bool):
    yield _chunk({"role": "assistant", "content": ""})
    if turn.tool_calls:
        yield _chunk({"tool_calls": [{"index": index, **call} for index, call in enumerate(_calls(turn))]})
        yield _chunk({}, "tool_calls")
    else:
        text = turn.text or ""
        size = -(-len(text) // turn.chunks) or 1
        for offset in range(0, len(text), size):
            if offset and turn.delay:
                await asyncio.sleep(turn.delay)
            yield _chunk({"content": text[offset : offset + size]})
        yield _chunk({}, "stop")
    if include_usage:
        yield _chunk(None, usage=True)
    yield "data: [DONE]\n\n"


def _message(turn: Turn) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": None if turn.tool_calls else turn.text or ""}
    if turn.tool_calls:
        message["tool_calls"] = _calls(turn)
    return {
        "id": "chatcmpl-scripted",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "scripted",
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if turn.tool_calls else "stop"}],
        "usage": USAGE,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    add_listener_arguments(parser)
    args = parser.parse_args()
    serve_fixture(app, fd=args.fd, shared_socket=args.shared_socket)
