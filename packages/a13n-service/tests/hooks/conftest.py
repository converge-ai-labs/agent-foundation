from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from a13n_service.database.metadata import service_metadata
from a13n_service.storage.config import SQLiteConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.interactions.conftest import _seed_interaction_database


@pytest.fixture
async def hook_interaction_sessions(
    service_sqlite_database: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_sql_engine(SQLiteConfig(path=service_sqlite_database))
    sessions = create_session_factory(engine)
    await _seed_interaction_database(sessions)
    try:
        yield sessions
    finally:
        await engine.dispose()


@pytest.fixture
async def hook_postgres_sessions(
    pg_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    from a13n_service.storage.config import PostgreSQLConfig

    engine = create_sql_engine(PostgreSQLConfig(url=pg_url))
    async with engine.begin() as connection:
        await connection.run_sync(service_metadata().create_all)
    sessions = create_session_factory(engine)
    await _seed_interaction_database(sessions)
    try:
        yield sessions
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(service_metadata().drop_all)
        await engine.dispose()
