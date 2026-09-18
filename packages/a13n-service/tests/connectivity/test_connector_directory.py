"""Persist complete app snapshots and fence uncertain shared OAuth creation."""

import asyncio
from datetime import timedelta

import pytest
from a13n_harness.providers.connector.contracts import ConnectorProviderError, DiscoveredConnector
from a13n_service.connectivity.connectors.domain import ConnectorProvider
from a13n_service.connectivity.connectors.errors import ConnectorError
from a13n_service.connectivity.connectors.models import ConnectorProviderRecord
from a13n_service.connectivity.connectors.service import ConnectorProviderService
from a13n_service.connectivity.connectors.shared_setup import reserve_shared_setup
from a13n_service.storage import short_session, transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, actor
from .connector_helpers import FakeConnectorProvider
from .test_connector_service import connector_backend as connector_backend
from .test_connector_service import connector_registry as connector_registry
from .test_connector_service import connector_services as connector_services
from .test_connector_service import create_connector

pytestmark = pytest.mark.anyio


async def test_directory_cache_search_paging_refresh_and_failure_are_atomic(
    connector_services, connectivity_sessions, monkeypatch
):
    providers, _ = connector_services
    provider = await create_connector(providers)
    calls = 0
    fail = False
    apps = tuple(
        DiscoveredConnector(
            key=key, name=key.title(), setup_schema={"type": "object"}, authentication_methods=("OAUTH2",)
        )
        for key in ("github", "gmail", "slack")
    )

    async def discover(self):
        nonlocal calls
        calls += 1
        if fail:
            raise ConnectorProviderError("upstream_unavailable")
        return apps

    monkeypatch.setattr(FakeConnectorProvider, "discover_connectors", discover)
    first = await providers.discover_connectors(actor=actor(), connector_provider_id=provider.id, query="g", limit=1)
    assert [item.key for item in first.items] == ["github"]
    second = await providers.discover_connectors(
        actor=actor(), connector_provider_id=provider.id, query="g", cursor=first.next_cursor, limit=1
    )
    assert [item.key for item in second.items] == ["gmail"] and second.next_cursor is None
    assert calls == 1
    with pytest.raises(ConnectorError, match="Directory changed"):
        await providers.discover_connectors(
            actor=actor(), connector_provider_id=provider.id, query="slack", cursor=first.next_cursor
        )
    fail = True
    with pytest.raises(ConnectorError):
        await providers.discover_connectors(actor=actor(), connector_provider_id=provider.id, refresh=True)
    cached = await providers.discover_connectors(actor=actor(), connector_provider_id=provider.id)
    assert len(cached.items) == 3 and calls == 2
    fail = False
    apps = apps[:1]
    providers._clock = lambda: NOW + timedelta(seconds=1)
    refreshed = await providers.discover_connectors(actor=actor(), connector_provider_id=provider.id, refresh=True)
    assert len(refreshed.items) == 1
    async with short_session(connectivity_sessions) as session:
        record = await session.get(ConnectorProviderRecord, provider.id)
        assert len(record.directory_json) == 1


async def test_shared_setup_claim_is_single_use_and_survives_uncertain_outcome(
    connector_services, connectivity_sessions
):
    providers, _ = connector_services
    provider = await create_connector(providers)
    await _assert_shared_setup_claim_is_single_use(connectivity_sessions, provider)


async def test_postgresql_shared_setup_claim_is_single_use(
    connectivity_sessions, connector_registry, credential_protector
):
    providers = ConnectorProviderService(
        connectivity_sessions, connector_registry, credential_protector, clock=lambda: NOW
    )
    provider = await create_connector(providers)
    await _assert_shared_setup_claim_is_single_use(connectivity_sessions, provider)


async def _assert_shared_setup_claim_is_single_use(
    sessions: async_sessionmaker[AsyncSession], provider: ConnectorProvider
) -> None:

    async def claim():
        await reserve_shared_setup(
            sessions,
            configuration_key="oauth",
            provider_id=provider.id,
            credential_generation=provider.credential_generation,
            connector_key="github",
        )

    results = await asyncio.gather(*(claim() for _ in range(16)), return_exceptions=True)
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, ConnectorProviderError) for result in results) == 15
    with pytest.raises(ConnectorProviderError, match="shared_setup_outcome_unknown"):
        await claim()
    async with transaction(sessions) as session:
        record = await session.get(ConnectorProviderRecord, provider.id)
        record.credential_generation += 1
    with pytest.raises(ConnectorProviderError, match="connector_provider_changed"):
        await claim()

    with pytest.raises(ConnectorProviderError, match="shared_setup_outcome_unknown"):
        await reserve_shared_setup(
            sessions,
            configuration_key="oauth",
            provider_id=provider.id,
            credential_generation=provider.credential_generation + 1,
            connector_key="github",
        )


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://malicious.example/schema"},
        {"type": "object", "properties": {"access_token": {"type": "string"}}},
        {"type": "object", "properties": {"option": {"type": "string", "writeOnly": True}}},
    ],
)
def test_discovery_rejects_unsafe_setup_schemas(schema) -> None:
    from a13n_harness.providers.connector.contracts import ConnectorProviderError, DiscoveredConnector
    from a13n_service.connectivity.connectors.discovery import validate_connectors

    with pytest.raises(ConnectorProviderError, match="unsafe_setup_schema"):
        validate_connectors(
            (DiscoveredConnector(key="github", name="GitHub", setup_schema=schema, authentication_methods=("oauth2",)),)
        )
