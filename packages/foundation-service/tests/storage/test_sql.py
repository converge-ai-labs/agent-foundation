import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from threading import Event

import anyio
import pytest
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import (
    async_database_url,
    create_session_factory,
    create_sql_engine,
    short_session,
    sync_database_url,
    transaction,
)
from sqlalchemy import Column, Integer, MetaData, String, Table, event, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

pytestmark = pytest.mark.anyio

metadata = MetaData()
records = Table(
    "storage_contract_records",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(100), nullable=False, unique=True),
)


@pytest.fixture(params=["sqlite", "postgresql"])
async def sql_resources(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    if request.param == "sqlite":
        config = SQLiteConfig(path=tmp_path / "database.sqlite3")
    else:
        config = PostgreSQLConfig(url=request.getfixturevalue("pg_url"))
    engine = create_sql_engine(config)
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    try:
        yield engine, create_session_factory(engine)
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.drop_all)
        await engine.dispose()


async def test_transaction_commits(sql_resources) -> None:
    _, sessions = sql_resources

    async with transaction(sessions) as session:
        await session.execute(insert(records).values(id=1, name="committed"))

    async with short_session(sessions) as session:
        assert (await session.execute(select(records.c.name))).scalar_one() == "committed"


async def test_transaction_rolls_back_on_error(sql_resources) -> None:
    _, sessions = sql_resources

    with pytest.raises(RuntimeError, match="abort"):
        async with transaction(sessions) as session:
            await session.execute(insert(records).values(id=1, name="rolled-back"))
            raise RuntimeError("abort")

    async with short_session(sessions) as session:
        assert (await session.execute(select(records))).all() == []


async def test_constraints_are_portable(sql_resources) -> None:
    _, sessions = sql_resources
    async with transaction(sessions) as session:
        await session.execute(insert(records).values(id=1, name="unique"))

    with pytest.raises(IntegrityError):
        async with transaction(sessions) as session:
            await session.execute(insert(records).values(id=2, name="unique"))


async def test_concurrent_tasks_use_independent_sessions(sql_resources) -> None:
    _, sessions = sql_resources

    async def write(index: int) -> None:
        async with transaction(sessions) as session:
            await session.execute(insert(records).values(id=index, name=f"record-{index}"))

    async with anyio.create_task_group() as tasks:
        for index in range(1, 6):
            tasks.start_soon(write, index)

    async with short_session(sessions) as session:
        names = (await session.execute(select(records.c.name).order_by(records.c.id))).scalars().all()
    assert names == [f"record-{index}" for index in range(1, 6)]


def test_postgresql_config_rejects_non_postgresql_url() -> None:
    with pytest.raises(ValueError, match="PostgreSQL backend"):
        create_sql_engine(PostgreSQLConfig(url="mysql://example/database"))


def test_database_urls_select_sync_and_async_drivers(tmp_path: Path) -> None:
    sqlite = SQLiteConfig(path=tmp_path / "database.sqlite3")
    postgresql = PostgreSQLConfig(url="postgresql://user:secret@example.test/database")

    assert async_database_url(sqlite).drivername == "sqlite+aiosqlite"
    assert sync_database_url(sqlite).drivername == "sqlite"
    assert async_database_url(postgresql).drivername == "postgresql+psycopg"
    assert sync_database_url(postgresql).drivername == "postgresql+psycopg"


@pytest.mark.parametrize("scope_factory", [short_session, transaction])
async def test_cancelled_write_rolls_back_and_releases_connection(sql_resources, scope_factory, caplog) -> None:
    engine, sessions = sql_resources
    returned = False
    with anyio.CancelScope() as cancellation:

        def cancel_write(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO storage_contract_records"):
                asyncio.get_running_loop().call_soon(cancellation.cancel)

        event.listen(engine.sync_engine, "before_cursor_execute", cancel_write)
        try:
            async with scope_factory(sessions) as session:
                await session.execute(insert(records).values(id=1, name="cancelled"))
                returned = True
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", cancel_write)

    assert cancellation.cancelled_caught
    assert not returned
    async with short_session(sessions) as session:
        assert (await session.execute(select(records))).all() == []
    assert engine.pool.checkedout() == 0
    assert "Exception terminating connection" not in caplog.text


async def test_cancellation_during_checkout_does_not_orphan_connection(sql_resources) -> None:
    engine, sessions = sql_resources
    await engine.dispose()
    entered = False
    with anyio.CancelScope() as cancellation:

        async def cancel_connect(driver_connection):
            cancellation.cancel()
            await anyio.lowlevel.checkpoint()

        def on_connect(connection, record):
            connection.run_async(cancel_connect)

        event.listen(engine.sync_engine, "connect", on_connect)
        try:
            async with short_session(sessions):
                entered = True
        finally:
            event.remove(engine.sync_engine, "connect", on_connect)

    assert cancellation.cancelled_caught
    assert not entered
    assert engine.pool.checkedout() == 0
    async with short_session(sessions) as session:
        assert (await session.execute(select(records))).all() == []


@pytest.mark.parametrize("consumer_fails", [False, True])
async def test_session_cleanup_timeout_is_visible_without_masking_consumer_error(
    sql_resources, monkeypatch: pytest.MonkeyPatch, consumer_fails: bool
) -> None:
    _, sessions = sql_resources
    close = AsyncSession.close

    async def blocked_close(session):
        await close(session)
        await anyio.sleep_forever()

    monkeypatch.setattr(AsyncSession, "close", blocked_close)
    failure = RuntimeError("consumer failed")
    with pytest.raises(RuntimeError if consumer_fails else TimeoutError) as caught:
        async with short_session(sessions, cleanup_timeout_seconds=0.01) as session:
            await session.execute(select(records))
            if consumer_fails:
                raise failure
    if consumer_fails:
        assert caught.value is failure
        assert "session cleanup failed with TimeoutError" in failure.__notes__


async def test_cancellation_during_commit_returns_connection_before_propagating(sql_resources, caplog) -> None:
    engine, sessions = sql_resources
    with anyio.CancelScope() as cancellation:

        def cancel_reset(connection, record, reset_state):
            cancellation.cancel()

        event.listen(engine.sync_engine, "reset", cancel_reset)
        try:
            async with transaction(sessions) as session:
                await session.execute(insert(records).values(id=1, name="committed"))
        finally:
            event.remove(engine.sync_engine, "reset", cancel_reset)

    assert cancellation.cancelled_caught
    assert engine.pool.checkedout() == 0
    async with short_session(sessions) as session:
        assert (await session.execute(select(records.c.name))).scalar_one() == "committed"
    assert "Exception" not in caplog.text


async def test_cancelled_sqlite_query_drains_worker_before_reusing_connection(tmp_path: Path) -> None:
    engine = create_sql_engine(SQLiteConfig(path=tmp_path / "cancel-query.sqlite3"))
    sessions = create_session_factory(engine)
    started, release = Event(), Event()
    cancellation = anyio.CancelScope()

    def blocked_query():
        started.set()
        if not release.wait(5):
            raise TimeoutError("query was not released")
        return 42

    async def install_function(driver):
        await driver.create_function("blocked_query", 0, blocked_query)

    def on_connect(connection, record):
        connection.run_async(install_function)

    event.listen(engine.sync_engine, "connect", on_connect)

    async def query():
        with cancellation:
            async with short_session(sessions) as session:
                await session.execute(text("SELECT blocked_query()"))
        assert cancellation.cancelled_caught

    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(query)
            try:
                assert await anyio.to_thread.run_sync(started.wait, 5)
                cancellation.cancel()
                await anyio.lowlevel.checkpoint()
            finally:
                release.set()
        assert engine.pool.checkedout() == 0
        async with short_session(sessions) as session:
            assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        release.set()
        await engine.dispose()
