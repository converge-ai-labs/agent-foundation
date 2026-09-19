"""A Connector definition, not Service, declares its browser-binding and setup lifetime."""

from datetime import timedelta

import pytest
from a13n_harness.providers.connector import ConnectorSetupPolicy
from a13n_service.connectivity.connectors.connections import ConnectorConnectionService
from a13n_service.connectivity.connectors.errors import ConnectorError
from a13n_service.connectivity.connectors.models import ConnectorAuthorizationRecord
from a13n_service.connectivity.connectors.service import ConnectorProviderService
from sqlalchemy import select

from .conftest import NOW, actor
from .connector_helpers import FakeConnectorBackend, fake_catalog
from .test_connector_service import create_connection, create_connector

pytestmark = pytest.mark.anyio


async def _services(sessions, protector, policy, *, public_origin="https://foundation.example"):
    catalog = fake_catalog(FakeConnectorBackend(), setup_policy=policy)
    connectors = ConnectorProviderService(sessions, catalog, None, protector, clock=lambda: NOW)
    connections = ConnectorConnectionService(
        sessions,
        catalog,
        None,
        protector,
        correlation_secret=b"c" * 32,
        public_origin=public_origin,
        setup_ttl_seconds=600,
        clock=lambda: NOW,
    )
    connector = await create_connector(connectors)
    connection = await create_connection(
        connections, connector_provider_id=connector.id, idempotency_key="create-connection"
    )
    return connections, connection


async def _start(connections, connection, *, browser_nonce):
    return await connections.start_setup(
        actor=actor(),
        connection_id=connection.id,
        idempotency_key="start-setup",
        expected_version=connection.version,
        setup={"scopes": ["read"]},
        browser_nonce=browser_nonce,
        return_url="/settings/connectors",
    )


async def test_declared_browser_binding_is_required_without_a_vendor_branch(
    connectivity_sessions, credential_protector
) -> None:
    connections, connection = await _services(
        connectivity_sessions, credential_protector, ConnectorSetupPolicy(requires_browser_binding=True)
    )
    with pytest.raises(ConnectorError) as caught:
        await _start(connections, connection, browser_nonce=None)
    assert caught.value.code == "browser_setup_required"


async def test_setup_without_a_declared_policy_needs_no_browser_binding(
    connectivity_sessions, credential_protector
) -> None:
    connections, connection = await _services(connectivity_sessions, credential_protector, ConnectorSetupPolicy())
    launch = await _start(connections, connection, browser_nonce=None)
    assert launch.connection.status == "pending"


async def test_declared_setup_ttl_caps_the_deployment_lifetime(connectivity_sessions, credential_protector) -> None:
    connections, connection = await _services(
        connectivity_sessions, credential_protector, ConnectorSetupPolicy(max_setup_ttl_seconds=60)
    )
    await _start(connections, connection, browser_nonce="b" * 64)
    async with connectivity_sessions() as session:
        attempt = await session.scalar(select(ConnectorAuthorizationRecord))
    assert attempt is not None and attempt.expires_at == NOW + timedelta(seconds=60)
