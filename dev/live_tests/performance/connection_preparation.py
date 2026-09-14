"""Establish explicit connection states outside operation sample timers."""

import asyncio
import logging
from contextlib import AsyncExitStack
from time import perf_counter

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def prepare_pg_connections(engine, *, state, count):
    # No operation owns a connection here. Dispose prevents earlier scenarios
    # and verification reads from silently warming a nominally cold wave.
    await engine.dispose()
    if state == "warm":
        async with AsyncExitStack() as stack:
            # Hold all checkouts until the pool has count physical connections;
            # repeated acquire/release would only warm one connection.
            for _ in range(count):
                connection = await stack.enter_async_context(engine.connect())
                assert await connection.scalar(text("SELECT 1")) == 1
    available = engine.pool.checkedin()
    assert available == (count if state == "warm" else 0)
    logger.info("pg_connection_preparation state=%s available=%s requested=%s", state, available, count)
    return available


async def warm_http_connections(client, count):
    """Hold distinct HTTP/1.1 responses until every connection is established."""
    streams = []
    ready = asyncio.Event()
    started = perf_counter()

    async def warm():
        async with client.stream("GET", "/healthz") as response:
            response.raise_for_status()
            streams.append(response.extensions["network_stream"])
            if len(streams) == count:
                ready.set()
            await ready.wait()
            # Consuming the body returns a reusable connection to the pool.
            await response.aread()

    async with asyncio.timeout(30), asyncio.TaskGroup() as group:
        for _ in range(count):
            group.create_task(warm())
    distinct = len({id(stream) for stream in streams})
    assert distinct == count, "HTTP warmup must establish one connection per caller"
    logger.info(
        "http_connection_preparation connections=%s elapsed_ms=%.2f", distinct, (perf_counter() - started) * 1000
    )
    return distinct
