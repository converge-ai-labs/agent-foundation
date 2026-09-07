from collections.abc import AsyncIterator
from pathlib import Path

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


@pytest.mark.parametrize("write", [False, True])
async def test_cancellation_during_database_io_finishes_cleanup_before_propagating(sql_resources, write) -> None:
    engine, sessions = sql_resources
    entered = anyio.Event()
    completed_after_scope = False
    is_sqlite = engine.dialect.name == "sqlite"
    if is_sqlite:
        import time

        async with engine.connect() as connection:
            await connection.run_sync(lambda sync: sync.connection.create_function("test_wait", 1, time.sleep))
    statement = "SELECT test_wait(0.05)" if is_sqlite else "SELECT pg_sleep(0.05)"

    def started(_conn, _cursor, sql, _parameters, _context, _many):
        if sql == statement:
            entered.set()

    event.listen(engine.sync_engine, "before_cursor_execute", started)

    async def query():
        nonlocal completed_after_scope
        context = transaction if write else short_session
        async with context(sessions) as session:
            if write:
                await session.execute(insert(records).values(id=99, name="cancelled"))
            await session.execute(text(statement))
        completed_after_scope = True

    try:
        with anyio.fail_after(2):
            async with anyio.create_task_group() as group:
                group.start_soon(query)
                await entered.wait()
                group.cancel_scope.cancel()
        assert not completed_after_scope
        assert engine.pool.checkedout() == 0
        async with short_session(sessions) as session:
            assert await session.scalar(text("SELECT 1")) == 1
            assert (await session.execute(select(records))).all() == []
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", started)


async def test_database_scope_honors_the_callers_earlier_deadline(sql_resources) -> None:
    engine, sessions = sql_resources
    if engine.dialect.name == "sqlite":
        import time

        async with engine.connect() as connection:
            await connection.run_sync(lambda sync: sync.connection.create_function("test_wait", 1, time.sleep))
        statement = "SELECT test_wait(0.1)"
    else:
        statement = "SELECT pg_sleep(0.1)"
    with pytest.raises(TimeoutError):
        with anyio.fail_after(0.01):
            async with short_session(sessions) as session:
                await session.execute(text(statement))
    assert engine.pool.checkedout() == 0
    async with short_session(sessions) as session:
        assert await session.scalar(text("SELECT 1")) == 1
