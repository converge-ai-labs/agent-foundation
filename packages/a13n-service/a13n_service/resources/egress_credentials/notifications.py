"""ID-only wakeups; durable credential versions remain the synchronization source."""

import asyncio
import json

from redis.asyncio import Redis
from redis.exceptions import RedisError

CHANNEL = "a13n:egress-credentials"


async def notify(redis: Redis, credential_id: str, version: int) -> None:
    try:
        async with asyncio.timeout(0.25):
            await redis.publish(CHANNEL, json.dumps({"credential_id": credential_id, "version": version}))
    except (RedisError, TimeoutError):
        pass  # The active owner's periodic comparison repairs a lost notification.
