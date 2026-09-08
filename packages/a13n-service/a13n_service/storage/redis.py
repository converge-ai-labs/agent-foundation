"""Redis-compatible client construction."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import cast

from anyio import fail_after, move_on_after
from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis
from redis.asyncio import Redis

from .config import RedisMemoryConfig, RedisServerConfig


@asynccontextmanager
async def open_redis(config: RedisServerConfig | RedisMemoryConfig) -> AsyncGenerator[Redis]:
    if isinstance(config, RedisServerConfig):
        client = Redis.from_url(
            config.url.get_secret_value(),
            decode_responses=False,
            max_connections=config.max_connections,
            socket_connect_timeout=config.connect_timeout_seconds,
            socket_timeout=config.command_timeout_seconds,
            health_check_interval=config.health_check_interval_seconds,
            retry_on_timeout=False,
        )
    else:
        server = FakeServer(version=config.server_version)
        client = cast(Redis, FakeRedis(server=server, decode_responses=False))

    try:
        yield client
    finally:
        with move_on_after(config.cleanup_timeout_seconds, shield=True):
            await client.aclose()


async def check_redis(client: Redis, *, timeout_seconds: float = 3) -> None:
    with fail_after(timeout_seconds):
        if not await client.ping():
            raise RuntimeError("Redis readiness check returned false")


def redis_memory_identity(client: Redis) -> str:
    """Identify an in-process server for scripts; real servers use INFO atomically."""
    if isinstance(client, FakeRedis):
        server = client.connection_pool.connection_kwargs.get("server")
        return f"memory:{id(server) if server is not None else id(client)}"
    return ""
