"""Bounded, expiring Redis Streams for one admitted EIP transfer, without replay."""

from __future__ import annotations

import asyncio
from typing import Literal

import anyio
from redis.asyncio import Redis
from redis.exceptions import RedisError

from a13n_service.runs.environments.clients.connections import owner_key

TTL_SECONDS = 300
MAX_FRAMES = 8

# Empty streams retain their last-generated ID. No data is trimmed to make room.
_CREATE = """
if redis.call('GET', KEYS[3]) ~= ARGV[1] then return 0 end
if redis.call('EXISTS', KEYS[1], KEYS[2]) ~= 0 then return 0 end
for i=1,2 do
  redis.call('XADD', KEYS[i], '0-1', 'init', '')
  redis.call('XDEL', KEYS[i], '0-1')
  redis.call('EXPIRE', KEYS[i], ARGV[2])
end
return 1
"""
_CHECK = """
if redis.call('GET', KEYS[3]) ~= ARGV[1] then return 0 end
if redis.call('EXISTS', KEYS[1], KEYS[2]) ~= 2 then return 0 end
redis.call('EXPIRE', KEYS[1], ARGV[2])
redis.call('EXPIRE', KEYS[2], ARGV[2])
return 1
"""
_SEND = """
if redis.call('GET', KEYS[3]) ~= ARGV[1] then return -1 end
if redis.call('EXISTS', KEYS[1], KEYS[2]) ~= 2 then return -1 end
if string.len(ARGV[3]) > tonumber(ARGV[4]) then return -1 end
redis.call('EXPIRE', KEYS[1], ARGV[2])
redis.call('EXPIRE', KEYS[2], ARGV[2])
if redis.call('XLEN', KEYS[1]) >= tonumber(ARGV[5]) then return 0 end
redis.call('XADD', KEYS[1], '*', 'frame', ARGV[3])
return 1
"""
_ACK = """
if redis.call('GET', KEYS[3]) ~= ARGV[1] then return 0 end
if redis.call('EXISTS', KEYS[1], KEYS[2]) ~= 2 then return 0 end
redis.call('XDEL', KEYS[1], ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[2])
redis.call('EXPIRE', KEYS[2], ARGV[2])
return 1
"""


def transfer_keys(environment_id: str, epoch: str, transfer_id: str) -> tuple[str, str]:
    prefix = f"a13n:client-environment:{environment_id}:transfer:{epoch}:{transfer_id}"
    return prefix + ":w2c", prefix + ":c2w"


class StreamPair:
    def __init__(
        self,
        redis: Redis,
        environment_id: str,
        epoch: str,
        transfer_id: str,
        *,
        side: Literal["worker", "control"],
        limit: int,
        seconds: float,
    ) -> None:
        # Use the runtime's no-retry routing client: a lost XADD reply must not replay a frame.
        self.redis, self.epoch, self.limit = redis, epoch, limit
        self.keys = transfer_keys(environment_id, epoch, transfer_id)
        self.owner = owner_key(environment_id)
        self.write_key, self.read_key = self.keys if side == "worker" else self.keys[::-1]
        self.cursor: str | bytes = "0-1"
        self.deadline = asyncio.get_running_loop().time() + seconds
        self.closed = False

    async def create(self) -> None:
        if not await self.redis.eval(_CREATE, 3, *self.keys, self.owner, self.epoch, TTL_SECONDS):
            raise OSError("Transfer ownership changed or streams already exist")

    async def send(self, raw: bytes) -> None:
        if self.closed or not raw or len(raw) > self.limit:
            raise OSError("Transfer is closed or frame exceeds its limit")
        if asyncio.get_running_loop().time() >= self.deadline:
            raise TimeoutError("Transfer deadline elapsed")
        async with asyncio.timeout_at(self.deadline):
            while True:
                result = await self.redis.eval(
                    _SEND,
                    3,
                    self.write_key,
                    self.read_key,
                    self.owner,
                    self.epoch,
                    TTL_SECONDS,
                    raw,
                    self.limit,
                    MAX_FRAMES,
                )
                if result < 0:
                    raise OSError("Transfer streams expired or ownership changed")
                if result == 1:
                    return
                await asyncio.sleep(0.02)

    async def receive(self) -> bytes:
        if asyncio.get_running_loop().time() >= self.deadline:
            raise TimeoutError("Transfer deadline elapsed")
        async with asyncio.timeout_at(self.deadline):
            while True:
                if self.closed or not await self.redis.eval(
                    _CHECK,
                    3,
                    *self.keys,
                    self.owner,
                    self.epoch,
                    TTL_SECONDS,
                ):
                    raise OSError("Transfer streams expired or ownership changed")
                # The shared client decodes ordinary RPC strings; this reply contains raw EIP bytes.
                rows = await self.redis.execute_command(
                    "XREAD",
                    "COUNT",
                    1,
                    "BLOCK",
                    1000,
                    "STREAMS",
                    self.read_key,
                    self.cursor,
                    NEVER_DECODE=True,
                )
                if not rows:
                    continue
                _, entries = rows[0]
                identity, fields = entries[0]
                raw = fields.get(b"frame")
                if not isinstance(raw, bytes) or not raw or len(raw) > self.limit:
                    raise OSError("Invalid transfer frame")
                if not await self.redis.eval(
                    _ACK,
                    3,
                    self.read_key,
                    self.write_key,
                    self.owner,
                    self.epoch,
                    TTL_SECONDS,
                    identity,
                ):
                    raise OSError("Transfer streams expired or ownership changed")
                self.cursor = identity
                return raw

    async def close(self) -> None:
        self.closed = True
        with anyio.move_on_after(1, shield=True):
            try:
                await self.redis.delete(*self.keys)
            except RedisError:
                pass  # A crashed/unreachable peer is bounded by the TTL as well as the transfer deadline.
