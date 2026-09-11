"""Real local pools prove distinct prewarmed connections and subsequent reuse."""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from types import SimpleNamespace

import httpx2
import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine

from ..performance.connection_preparation import prepare_pg_connections, warm_http_connections


@pytest.mark.anyio
@pytest.mark.parametrize("state", ["cold", "warm"])
async def test_sql_pool_state_controls_physical_connections_before_the_wave(tmp_path, state):
    # SQLite exercises the real SQLAlchemy pool without Docker; live tests
    # separately verify PostgreSQL authentication, transactions and persistence.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pool.db'}", pool_size=8, max_overflow=0)
    opened = []
    event.listen(engine.sync_engine, "connect", lambda connection, record: opened.append(connection))
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        await prepare_pg_connections(engine, state=state, count=8)
        before_wave = len(opened)
        assert engine.pool.checkedin() == (8 if state == "warm" else 0)
        async with AsyncExitStack() as stack:
            for _ in range(8):
                connection = await stack.enter_async_context(engine.connect())
                assert await connection.scalar(text("SELECT 1")) == 1
        assert len(opened) - before_wave == (0 if state == "warm" else 8)
        assert engine.pool.checkedout() == 0
    finally:
        await engine.dispose()


@asynccontextmanager
async def http_peer(*, fail=False):
    tasks, ports = set(), set()

    async def serve(reader, writer):
        tasks.add(asyncio.current_task())
        ports.add(writer.get_extra_info("peername")[1])
        try:
            while True:
                await reader.readuntil(b"\r\n\r\n")
                status = b"503 Service Unavailable" if fail else b"200 OK"
                writer.write(b"HTTP/1.1 " + status + b"\r\nContent-Length: 2\r\n\r\nOK")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", ports
    finally:
        server.close()
        await server.wait_closed()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.anyio
async def test_http_warmup_opens_256_connections_and_the_next_wave_reuses_them():
    from ..performance.service_fixtures import ServiceFixture

    observer = SimpleNamespace(http_connections=0)

    async def trace(name, info):
        await ServiceFixture.trace_http(observer, name, info)

    async with http_peer() as (origin, ports):
        async with httpx2.AsyncClient(
            base_url=origin,
            trust_env=False,
            limits=httpx2.Limits(max_connections=256, max_keepalive_connections=256, keepalive_expiry=60),
        ) as client:
            response = await client.get("/healthz", extensions={"trace": trace})
            assert response.status_code == 200 and observer.http_connections == 1
            assert await warm_http_connections(client, 256) == 256
            assert len(ports) == 256
            warmed_ports = set(ports)
            assert await warm_http_connections(client, 256) == 256
            assert ports == warmed_ports
            responses = await asyncio.gather(*(client.get("/healthz", extensions={"trace": trace}) for _ in range(256)))
            assert all(response.status_code == 200 for response in responses)
            assert observer.http_connections == 1, "Run preparation tracing must distinguish reuse from new TCP"


@pytest.mark.anyio
async def test_failed_http_warmup_does_not_leave_a_barrier_or_connection_held():
    async with http_peer(fail=True) as (origin, _):
        async with httpx2.AsyncClient(
            base_url=origin, trust_env=False, limits=httpx2.Limits(max_connections=8, max_keepalive_connections=8)
        ) as client:
            async with asyncio.timeout(5):
                with pytest.raises(ExceptionGroup):
                    await warm_http_connections(client, 8)
                responses = await asyncio.gather(*(client.get("/healthz") for _ in range(8)))
                assert all(response.status_code == 503 for response in responses)
