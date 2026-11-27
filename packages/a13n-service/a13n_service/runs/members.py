"""Best-effort worker counts. Only PostgreSQL grants an attempt's ownership."""

import asyncio
import hashlib
import math

from a13n_logging import get_logger
from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = get_logger(__name__)

# Redis time avoids clock skew between workers. Scores are expiry times; pruning and
# counting happen atomically. Expire the whole set too when every process disappears.
_REFRESH = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[1])
redis.call('PEXPIRE', KEYS[1], ARGV[2])
return redis.call('ZCARD', KEYS[1])
"""

FALLBACK_BATCH = 8


class Members:
    def __init__(self, redis: Redis, scope: str, worker_id: str, *, ttl: float, timeout: float):
        self.redis, self.worker_id = redis, worker_id
        self.key = "a13n:workers:" + hashlib.sha256(scope.encode()).hexdigest()
        self.ttl_ms, self.timeout = math.ceil(ttl * 1000), timeout
        self._unavailable = False

    async def refresh(self) -> int | None:
        try:
            async with asyncio.timeout(self.timeout):
                count = int(await self.redis.eval(_REFRESH, 1, self.key, self.worker_id, self.ttl_ms))
        except (RedisError, TimeoutError) as error:
            if not self._unavailable:
                logger.warning(
                    "Worker membership unavailable; limiting claims", extra={"error_type": type(error).__name__}
                )
            self._unavailable = True
            return None
        if self._unavailable:
            logger.info("Worker membership recovered")
        self._unavailable = False
        return count

    async def leave(self) -> None:
        try:
            async with asyncio.timeout(self.timeout):
                await self.redis.zrem(self.key, self.worker_id)
        except (RedisError, TimeoutError):
            # A lost leave only temporarily depresses other workers' claim sizes.
            logger.warning("Worker membership removal unavailable; awaiting expiry")
