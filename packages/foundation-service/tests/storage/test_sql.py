from collections.abc import AsyncIterator
from pathlib import Path

import anyio
import pytest
from converge_foundation_service.storage.config import PostgreSQLConfig, SQLiteConfig
from converge_foundation_service.storage.relational import (
    async_database_url,
    create_session_factory,
    create_sql_engine,
    short_session,
    sync_database_url,
    transaction,
)
from sqlalchemy import Column, Integer, MetaData, String, Table, insert, select
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
