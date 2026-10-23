"""Bounded Shell deltas; cache publication is independent of execution and lease renewal."""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from a13n_harness.toolsets.shell_observation import ShellObservation, ShellStream
from a13n_logging import exception_details, get_logger
from pydantic import BaseModel, ConfigDict
from redis.exceptions import RedisError

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)
_TTL_SECONDS = 10
_REFRESH_SECONDS = 5
_BLOCK_BYTES = 64 * 1024
_STREAM_BLOCKS = 16
_MAX_BYTES = 4 * 1024 * 1024

# One atomic cache revision, including expiry. A delta cannot recreate an evicted
# key without its earlier blocks. The next flush reseeds from the bounded local copy.
_PUBLISH = """
if ARGV[1] == '1' then
    redis.call('DEL', KEYS[1])
elseif redis.call('EXISTS', KEYS[1]) == 0 then
    return 0
end
for _, field in ipairs(cjson.decode(ARGV[3])) do redis.call('HDEL', KEYS[1], field) end
for field, value in pairs(cjson.decode(ARGV[4])) do redis.call('HSET', KEYS[1], field, value) end
redis.call('EXPIRE', KEYS[1], ARGV[2])
return 1
"""


def key(execution_id: str) -> str:
    return f"a13n:tool-output:{execution_id}"


class Snapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    thread_id: str
    worker_id: str
    observation: ShellObservation


class _Metadata(BaseModel):
    snapshot: Snapshot
    stdout: tuple[tuple[int, int], ...]
    stderr: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _Window:
    stdout: dict[int, bytes]
    stderr: dict[int, bytes]
    fields: dict[str, str]


def _append(previous: dict[int, bytes], stream: ShellStream) -> dict[int, bytes]:
    blocks = previous.copy()
    offset, data = stream.start_offset, stream.data
    if blocks:
        last = next(reversed(blocks))
        end = last + len(blocks[last])
        if offset != end and data:
            # A new provider range or a replacement snapshot starts a new window.
            blocks.clear()
        elif data and len(blocks[last]) < _BLOCK_BYTES:
            take = min(_BLOCK_BYTES - len(blocks[last]), len(data))
            blocks[last] += data[:take]
            offset, data = offset + take, data[take:]
    for start in range(0, len(data), _BLOCK_BYTES):
        blocks[offset + start] = data[start : start + _BLOCK_BYTES]
    # A provider can evict its prefix even on a silent observation.
    blocks = {start: chunk for start, chunk in blocks.items() if start + len(chunk) > stream.capture.available_start}
    while len(blocks) > _STREAM_BLOCKS:
        del blocks[next(iter(blocks))]
    return blocks


def _window(previous: _Window | None, snapshot: Snapshot) -> _Window:
    observation = snapshot.observation
    stdout = _append(previous.stdout if previous else {}, observation.stdout)
    stderr = _append(previous.stderr if previous else {}, observation.stderr)
    fields: dict[str, str] = {}
    for name, blocks, old in (
        ("o", stdout, previous.stdout if previous else {}),
        ("e", stderr, previous.stderr if previous else {}),
    ):
        for start, chunk in blocks.items():
            field = f"{name}:{start}"
            fields[field] = (
                previous.fields[field]
                if previous and old.get(start) == chunk
                else base64.b64encode(chunk).decode("ascii")
            )
    metadata = _Metadata(
        snapshot=snapshot.model_copy(
            update={
                "observation": observation.model_copy(
                    update={
                        "stdout": observation.stdout.model_copy(
                            update={"data": b"", "start_offset": next(iter(stdout), observation.stdout.start_offset)}
                        ),
                        "stderr": observation.stderr.model_copy(
                            update={"data": b"", "start_offset": next(iter(stderr), observation.stderr.start_offset)}
                        ),
                    }
                )
            }
        ),
        stdout=tuple((start, len(chunk)) for start, chunk in stdout.items()),
        stderr=tuple((start, len(chunk)) for start, chunk in stderr.items()),
    )
    fields["meta"] = metadata.model_dump_json()
    return _Window(stdout, stderr, fields)


def _restore(
    stream: ShellStream, sizes: tuple[tuple[int, int], ...], prefix: str, fields: dict[str, str]
) -> ShellStream:
    chunks = []
    offset = stream.start_offset
    if len(sizes) > _STREAM_BLOCKS:
        raise ValueError("Too many cached blocks")
    for start, size in sizes:
        data = base64.b64decode(fields[f"{prefix}:{start}"], validate=True)
        if start != offset or len(data) != size or size > _BLOCK_BYTES:
            raise ValueError("Incomplete cached range")
        chunks.append(data)
        offset += size
    return stream.model_copy(update={"data": b"".join(chunks)})


class Output:
    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self._pending: dict[str, _Window] = {}
        self._buffers: dict[str, _Window] = {}
        self._sent: dict[str, tuple[_Window, float]] = {}

    def offer(self, execution_id: str, snapshot: Snapshot) -> None:
        window = _window(self._buffers.get(execution_id), snapshot)
        self._buffers[execution_id] = self._pending[execution_id] = window

    def forget(self, execution_id: str) -> None:
        self._pending.pop(execution_id, None)
        self._buffers.pop(execution_id, None)
        self._sent.pop(execution_id, None)
        # Redis expiry bounds a concurrent write; DB terminal state wins on reads.

    async def get(self, thread_id: str, execution_id: str, worker_id: str) -> ShellObservation | None:
        try:
            async with asyncio.timeout(min(1, self.runtime.settings.redis.timeout)):
                # Runtime's shared Redis client uses decode_responses=True.
                fields = cast(dict[str, str], await self.runtime.redis.hgetall(key(execution_id)))
            if not fields or sum(len(value) for value in fields.values()) > _MAX_BYTES:
                return None
            metadata = _Metadata.model_validate_json(fields["meta"])
            snapshot = metadata.snapshot
            if snapshot.thread_id != thread_id or snapshot.worker_id != worker_id:
                return None
            observation = snapshot.observation
            return observation.model_copy(
                update={
                    "stdout": _restore(observation.stdout, metadata.stdout, "o", fields),
                    "stderr": _restore(observation.stderr, metadata.stderr, "e", fields),
                }
            )
        except (RedisError, TimeoutError, ValueError, KeyError):
            return None

    async def flush(self) -> None:
        self._pending.clear()
        current = asyncio.get_running_loop().time()
        sent: dict[str, _Window] = {}
        try:
            async with (
                asyncio.timeout(min(1, self.runtime.settings.redis.timeout)),
                self.runtime.redis.pipeline(transaction=False) as pipe,
            ):
                for execution_id, window in tuple(self._buffers.items()):
                    previous = self._sent.get(execution_id)
                    old = previous[0].fields if previous else {}
                    changes = {field: value for field, value in window.fields.items() if old.get(field) != value}
                    removed = list(old.keys() - window.fields.keys())
                    if not changes and not removed and previous and current - previous[1] < _REFRESH_SECONDS:
                        continue
                    pipe.eval(
                        _PUBLISH,
                        1,
                        key(execution_id),
                        int(previous is None),
                        _TTL_SECONDS,
                        json.dumps(removed),
                        json.dumps(changes),
                    )
                    sent[execution_id] = window
                results = await pipe.execute() if sent else []
        except (RedisError, TimeoutError) as error:
            # A lost response may follow a successful write. Reseed idempotently;
            # never retry an operation or discard deltas offered during this await.
            for execution_id in sent:
                self._sent.pop(execution_id, None)
            logger.warning("Shell output cache unavailable", extra={"exception_details": exception_details(error)})
        else:
            for (execution_id, window), published in zip(sent.items(), results, strict=True):
                if execution_id not in self._buffers:
                    continue
                if published:
                    self._sent[execution_id] = (window, current)
                else:
                    self._sent.pop(execution_id, None)

    async def run(self) -> None:
        while True:
            await asyncio.sleep(1)
            await self.flush()
