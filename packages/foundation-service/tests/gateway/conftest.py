from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from a13n_service.storage.config import SQLiteConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.interactions.conftest import _seed_interaction_database


@pytest.fixture
async def lifecycle_interaction_sessions(
    service_sqlite_database: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_sql_engine(SQLiteConfig(path=service_sqlite_database))
    sessions = create_session_factory(engine)
    await _seed_interaction_database(sessions)
    try:
        yield sessions
    finally:
        await engine.dispose()
