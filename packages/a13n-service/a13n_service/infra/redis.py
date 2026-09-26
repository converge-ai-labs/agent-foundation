"""Redis as a best-effort accelerator: rate limits, the claim wakeup marker and capped live streams.

Nothing here is authority. PostgreSQL decides ownership and durable state; every caller must behave
correctly, only slower, when Redis is unavailable or has lost data.
"""

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any

from a13n_logging import exception_details, get_logger
from redis.asyncio import Redis
from redis.exceptions import RedisError

from a13n_service.infra.errors import ServiceError, rate_limited

logger = get_logger(__name__)

WAKE_KEY = "a13n:wake"

_RATE_LIMIT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {count, redis.call('TTL', KEYS[1])}
"""


async def rate_limit(client: Redis, identity: str, *, limit: int, window_seconds: int) -> None:
    """Refuse `identity` past `limit` calls per window; while Redis cannot count, the call proceeds unlimited."""
    key = "a13n:rate:" + hashlib.sha256(identity.encode()).hexdigest()
    try:
        count, remaining = await client.eval(_RATE_LIMIT, 1, key, window_seconds)
    except RedisError as error:
        logger.warning(
            "Rate limit not enforced: Redis unavailable",
            extra={"error_type": type(error).__name__, "exception_details": exception_details(error)},
        )
        return
    if count > limit:
        raise rate_limited("Too many requests", max(1, remaining))


async def wake(client: Redis, *, timeout: float) -> None:
    """Leave one "look now" marker for idle workers; a burst of wakes coalesces into one marker."""
    try:
        async with asyncio.timeout(timeout), client.pipeline(transaction=True) as pipe:
            pipe.rpush(WAKE_KEY, "1")
            pipe.ltrim(WAKE_KEY, -1, -1)
            await pipe.execute()
    except (RedisError, TimeoutError):
        pass  # The periodic scan finds the run within one interval.


async def wait_for_wake(client: Redis, *, timeout: float) -> None:
    """The claim loop's timer: return on a marker or after `timeout`; a Redis error sleeps the same interval."""
    try:
        await client.blpop([WAKE_KEY], timeout=timeout)
    except RedisError:
        await asyncio.sleep(timeout)


@dataclass(frozen=True, slots=True)
class StreamEntry:
    key: str
    id: str
    fields: dict[str, str]


def _unavailable() -> ServiceError:
    return ServiceError("unavailable", "Live stream is unavailable", {"dependency": "redis"})


def _entries(key: str, raw: Any) -> list[StreamEntry]:
    # The client decodes responses, so IDs and fields are strings.
    return [StreamEntry(key=key, id=entry_id, fields=dict(values)) for entry_id, values in raw or ()]


async def append(
    client: Redis, key: str, entries: list[dict[str, str]], *, max_length: int, ttl: int, timeout: float
) -> list[str]:
    """Append in one round trip under an approximate length cap; the new entries' IDs, none when Redis dropped
    them."""
    try:
        async with asyncio.timeout(timeout), client.pipeline(transaction=False) as pipe:
            for fields in entries:
                pipe.xadd(key, fields, maxlen=max_length, approximate=True)  # type: ignore[arg-type]
            pipe.expire(key, ttl)
            *ids, _ = await pipe.execute()
        return ids
    except (RedisError, TimeoutError):
        return []


async def trim(client: Redis, key: str, *, min_id: str, timeout: float) -> None:
    """Remove the entries before `min_id`; after a failure the length cap and expiry remove them later."""
    try:
        async with asyncio.timeout(timeout):
            await client.xtrim(key, minid=min_id, approximate=False)
    except (RedisError, TimeoutError):
        pass


async def last_id(client: Redis, key: str) -> str:
    """The newest entry's ID, or the stream origin when the stream is empty or missing."""
    try:
        newest = _entries(key, await client.xrevrange(key, count=1))
    except RedisError:
        raise _unavailable() from None
    return newest[0].id if newest else "0-0"


async def read_entry(client: Redis, key: str, entry_id: str) -> StreamEntry | None:
    """One retained entry by ID; None once trimming or expiry removed it."""
    try:
        found = _entries(key, await client.xrange(key, min=entry_id, max=entry_id, count=1))
    except RedisError:
        raise _unavailable() from None
    return found[0] if found else None


async def read_range(client: Redis, key: str, *, after: str, until: str, count: int) -> list[StreamEntry]:
    """Entries after `after` up to and including `until`, oldest first."""
    try:
        return _entries(key, await client.xrange(key, min=f"({after}", max=until, count=count))
    except RedisError:
        raise _unavailable() from None


async def read(client: Redis, cursors: dict[str, str], *, count: int, block_ms: int) -> list[StreamEntry]:
    """One blocking read over many stream keys; `unavailable` when Redis cannot answer."""
    try:
        result: Any = await client.xread(cursors, count=count, block=block_ms)  # type: ignore[arg-type]
    except RedisError:
        raise _unavailable() from None
    return [entry for key, entries in result or () for entry in _entries(key, entries)]
