"""Process-multiplexed Redis bridge for the lifetime of one realtime Attempt."""

from __future__ import annotations

import asyncio
import struct
from collections import deque
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4

from a13n_logging import get_logger
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.exceptions import RedisError

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.telemetry import meter

if TYPE_CHECKING:
    from a13n_service.runs.attempts import Lease

ATTACH_SECONDS = 20
HEARTBEAT_SECONDS = 1
_PEER_TIMEOUT = 6
_STREAM_LIMIT = 512
_COMMIT_WAIT = 0.15
_PCM_HEADER = struct.Struct("!QQ")


PCM_DISCONTINUITIES = meter.create_counter(
    "a13n.realtime.pcm.discontinuities", unit="{event}", description="Detected realtime PCM discontinuities"
)
CONTROL_OVERFLOWS = meter.create_counter(
    "a13n.realtime.control.overflows",
    unit="{event}",
    description="Realtime bridges closed on a control gap or overflow",
)


class TransportError(ServiceError):
    def __init__(self, message: str = "The realtime transport is unavailable") -> None:
        super().__init__("unavailable", message)


class AttachTimeout(TransportError):
    pass


def unavailable() -> TransportError:
    return TransportError()


class Offer(BaseModel):
    model_config = ConfigDict(frozen=True)
    control_id: str
    audio: bool
    draft: str = ""
    max_duration_seconds: int | None = Field(default=None, ge=30)
    principal_id: str
    workspace_id: str


class Bridge:
    """Two independent subscriptions shared by every local call. Start lazily so Redis is optional for batch Runs."""

    def __init__(self, redis: Redis):
        self.redis = redis
        self.id = uuid4().hex
        self.calls: dict[bytes, Pipe] = {}
        self.lock = asyncio.Lock()
        self.listeners: AsyncExitStack | None = None

    async def start(self) -> None:
        async with self.lock:
            if self.listeners is not None:
                return
            async with AsyncExitStack() as stack:
                for lane in ("control", "pcm"):
                    sub = await stack.enter_async_context(self.redis.pubsub())
                    await sub.subscribe(f"a13n:rt:{self.id}:{lane}")
                    if not await sub.get_message(timeout=_PEER_TIMEOUT):
                        raise unavailable()
                    task = asyncio.create_task(self._listen(sub, pcm=lane == "pcm"))
                    stack.push_async_callback(self._stop, task)
                self.listeners = stack.pop_all()

    async def close(self) -> None:
        if self.listeners is not None:
            await self.listeners.aclose()

    async def _stop(self, task: asyncio.Task) -> None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _listen(self, sub: PubSub, *, pcm: bool) -> None:
        while True:
            try:
                async for message in sub.listen():
                    if message["type"] != "message":
                        continue
                    address, _, payload = message["data"].partition(b" ")
                    pipe = self.calls.get(address)
                    if pipe is None:
                        continue
                    if pcm:
                        fence, _, audio = payload.partition(b" ")
                        if fence != pipe.fence or len(audio) <= _PCM_HEADER.size:
                            continue
                        pipe.audio.append(audio)
                    else:
                        pipe.scan = True
                    pipe.wake.set()
            except RedisError:
                for pipe in tuple(self.calls.values()):
                    pipe.failed = True
                    pipe.wake.set()
                await asyncio.sleep(HEARTBEAT_SECONDS)


class Pipe:
    def __init__(self, bridge: Bridge, run_id: str, *, side: Literal["control", "worker"]):
        self.bridge, self.redis = bridge, bridge.redis
        self.side = side
        self.base = f"a13n:rt:{run_id}"
        other = "worker" if side == "control" else "control"
        self.inbox, self.outbox = f"{self.base}:{side}", f"{self.base}:{other}"
        self.offer = self.base + ":offer"
        self.control_id = bridge.id if side == "control" else ""
        self.attempt_id = ""
        self.peer = ""
        self.cursor = b"0-0"
        self.pending: deque[tuple[bytes, float]] = deque()
        self.audio: deque[bytes] = deque(maxlen=32)
        self.wake = asyncio.Event()
        self.scan = True
        self.failed = False
        self.sent = self.received = self.epoch = 0
        self.segment = 0
        self.segment_gap = False
        self.resumed_after_gap = False
        self.explicit_commits = False
        self.control_sent = self.control_received = 0
        self.last_check = 0.0
        self.attach_deadline = asyncio.get_running_loop().time() + ATTACH_SECONDS
        self.check_heartbeat_after: float | None = None

    @property
    def fence(self) -> bytes:
        return f"{self.control_id}/{self.attempt_id}".encode()

    @asynccontextmanager
    async def handshake(self) -> AsyncIterator[None]:
        try:
            async with asyncio.timeout_at(self.attach_deadline):
                yield
        except TimeoutError:
            raise AttachTimeout() from None
        self.check_heartbeat_after = asyncio.get_running_loop().time() + _PEER_TIMEOUT

    async def publish_offer(self, offer: Offer) -> None:
        await self.redis.set(self.offer, offer.model_dump_json(), ex=ATTACH_SECONDS)

    async def attach(self, lease: Lease) -> Offer:
        while (raw := await self.redis.getdel(self.offer)) is None:
            if await self.redis.exists(self.inbox + ":close"):
                raise unavailable()
            await asyncio.sleep(0.05)
        offer = Offer.model_validate_json(raw)
        if offer.workspace_id != lease.workspace_id:
            raise unavailable()
        self.peer = self.control_id = offer.control_id
        self.attempt_id = lease.attempt_id
        return offer

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[None]:
        await self.bridge.start()
        address = self.inbox.encode()
        self.bridge.calls[address] = self
        try:
            yield
        finally:
            self.bridge.calls.pop(address, None)

    async def shutdown(self) -> None:
        if self.side != "control" and not self.peer:
            return
        try:
            async with self.redis.pipeline(transaction=False) as batch:
                batch.set(self.outbox + ":close", self.fence, ex=ATTACH_SECONDS * 2)
                if self.peer:
                    batch.publish(f"a13n:rt:{self.peer}:control", self.outbox.encode())
                await batch.execute()
        except RedisError as error:
            get_logger(__name__).warning(
                "Realtime peer close notification failed", extra={"error_type": type(error).__name__}
            )

    async def send(self, frame: bytes) -> None:
        try:
            if frame == b"ping":
                async with self.redis.pipeline(transaction=False) as batch:
                    batch.set(self.inbox + ":alive", self.fence, ex=_PEER_TIMEOUT)
                    batch.expire(self.outbox, ATTACH_SECONDS * 2)
                    await batch.execute()
                return
            if frame.startswith(b"b"):
                self.sent += 1
                generation = self.segment if self.side == "control" else self.epoch
                packet = _PCM_HEADER.pack(self.sent, generation) + frame[1:]
                await self.redis.publish(
                    f"a13n:rt:{self.peer}:pcm", self.outbox.encode() + b" " + self.fence + b" " + packet
                )
                return
            if frame.startswith(b"commit:"):
                self.segment += 1
            self.control_sent += 1
            first = self.control_sent == 1
            # Create the stream and its TTL atomically. Later appends must not recreate it.
            async with self.redis.pipeline(transaction=first) as batch:
                batch.xadd(
                    self.outbox,
                    {b"seq": self.control_sent, b"fence": self.fence, b"frame": frame},
                    maxlen=_STREAM_LIMIT,
                    approximate=False,
                    nomkstream=not first,
                )
                if first:
                    batch.expire(self.outbox, ATTACH_SECONDS * 2)  # Heartbeats renew the stream TTL.
                batch.publish(f"a13n:rt:{self.peer}:control", self.outbox.encode())
                if (await batch.execute())[0] is None:
                    raise unavailable()
        except RedisError:
            raise unavailable() from None

    async def receive(self) -> bytes:
        try:
            while True:
                self.wake.clear()
                if self.failed:
                    raise unavailable()
                clock = asyncio.get_running_loop().time()
                periodic = clock - self.last_check >= HEARTBEAT_SECONDS
                if self.scan or periodic:
                    self.scan = False
                    async with self.redis.pipeline(transaction=False) as batch:
                        batch.mget(self.inbox + ":close", self.outbox + ":alive")
                        batch.xread({self.inbox: self.cursor}, count=_STREAM_LIMIT)
                        markers, rows = await batch.execute(raise_on_error=False)
                    if isinstance(markers, RedisError):
                        raise markers
                    close, alive = markers
                    if close == self.fence or (self.side == "worker" and close == self.control_id.encode() + b"/"):
                        return b"closed"
                    if periodic:
                        self.last_check = clock
                        if (
                            self.check_heartbeat_after is not None
                            and clock >= self.check_heartbeat_after
                            and alive != self.fence
                        ):
                            raise unavailable()
                    # Check stream command errors only after applying the close and heartbeat markers.
                    if isinstance(rows, RedisError):
                        raise rows
                    for _, entries in cast(list[tuple[bytes, list[tuple[bytes, dict[bytes, bytes]]]]], rows):
                        for cursor, fields in entries:
                            self.cursor = cursor
                            frame = fields[b"frame"]
                            fence = fields[b"fence"]
                            if (
                                self.side == "control"
                                and not self.peer
                                and frame.startswith(b"a")
                                and fence.startswith(self.control_id.encode() + b"/")
                            ):
                                self.attempt_id, self.peer = frame[1:].decode().split("/")
                            if fence != self.fence:
                                continue
                            self.control_received += 1
                            if int(fields[b"seq"]) != self.control_received or len(self.pending) >= _STREAM_LIMIT:
                                self.failed = True
                                CONTROL_OVERFLOWS.add(1, {"side": self.side})
                                raise unavailable()
                            self.pending.append((frame, clock))
                commit_deadline = None
                if self.pending:
                    frame, arrived = self.pending[0]
                    if not frame.startswith(b"commit:"):
                        self.pending.popleft()
                        return frame
                    last_seq = int(frame[7:])
                    commit_deadline = arrived + _COMMIT_WAIT
                    buffered = self.audio and _PCM_HEADER.unpack_from(self.audio[0])[1] <= self.segment
                    if self.received >= last_seq or (clock >= commit_deadline and not buffered):
                        self.pending.popleft()
                        gap = self.received != last_seq
                        self.explicit_commits = True
                        self.received = max(self.received, last_seq)
                        self.segment += 1
                        if self.segment_gap:
                            self.segment_gap = False
                            if self.resumed_after_gap:
                                self.resumed_after_gap = False
                                # Continuous input may have resumed after the reset before its first commit arrived.
                                return b"reset"
                            continue
                        if gap:
                            PCM_DISCONTINUITIES.add(1, {"direction": "ingress"})
                            return b"gap"
                        return b"commit"
                if self.audio:
                    packet = self.audio[0]
                    seq, generation = _PCM_HEADER.unpack_from(packet)
                    # Later input may arrive before the Stream announces the preceding commit.
                    if self.side == "control" or generation <= self.segment:
                        self.audio.popleft()
                        if seq <= self.received:
                            continue
                        gap = seq != self.received + 1
                        self.received = seq
                        if self.side == "control":
                            if generation != self.epoch:
                                continue
                        elif self.segment_gap and (self.explicit_commits or commit_deadline is not None):
                            continue
                        elif gap:
                            # An explicit commit gives us a known boundary to discard through.
                            # Auto-VAD has no commit: resume live PCM after the provider reset.
                            self.segment_gap = True
                            self.resumed_after_gap = False
                            PCM_DISCONTINUITIES.add(1, {"direction": "ingress"})
                            return b"gap"
                        elif self.segment_gap:
                            self.resumed_after_gap = True
                        return b"b" + packet[_PCM_HEADER.size :]
                try:
                    await asyncio.wait_for(
                        self.wake.wait(),
                        min(HEARTBEAT_SECONDS, max(0, commit_deadline - clock))
                        if commit_deadline is not None
                        else HEARTBEAT_SECONDS,
                    )
                except TimeoutError:
                    pass
        except RedisError:
            raise unavailable() from None

    async def discard_offer(self) -> None:
        try:
            await self.redis.delete(self.offer)
        except RedisError:
            pass
