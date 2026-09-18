from collections.abc import AsyncIterator

import pytest
from a13n_service.provider_plugins import load_provider_catalogs
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from a13n_service.web.registry import WebProviderRegistry
from a13n_service.web.service import WebProviderService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models.conftest import protector, seed_models


@pytest.fixture
async def web_sessions(service_database: PostgreSQLConfig) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_sql_engine(service_database)
    sessions = create_session_factory(engine)
    await seed_models(sessions)
    try:
        yield sessions
    finally:
        await engine.dispose()


@pytest.fixture
def web_service(web_sessions) -> WebProviderService:
    return WebProviderService(web_sessions, protector(), WebProviderRegistry(load_provider_catalogs(()).web))


def provider_transport(handler):
    import httpx2
    from a13n_harness.providers.web import WebProviderTransport

    class EndpointPolicy:
        async def validate(self, endpoint):
            assert endpoint in {"https://api.exa.ai/search", "https://api.exa.ai/contents"}
            return endpoint

    return WebProviderTransport(
        client_factory=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        endpoint_policy=EndpointPolicy(),
    )
