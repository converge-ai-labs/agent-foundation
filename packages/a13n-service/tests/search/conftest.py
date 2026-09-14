from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from a13n_service.search.service import SearchProviderService
from a13n_service.storage.config import SQLiteConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models.conftest import protector, seed_models


@pytest.fixture
async def search_sessions(service_sqlite_database: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_sql_engine(SQLiteConfig(path=service_sqlite_database))
    sessions = create_session_factory(engine)
    await seed_models(sessions)
    try:
        yield sessions
    finally:
        await engine.dispose()


@pytest.fixture
def search_service(search_sessions) -> SearchProviderService:
    return SearchProviderService(search_sessions, protector())
