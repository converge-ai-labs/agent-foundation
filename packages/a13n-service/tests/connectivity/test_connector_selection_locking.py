"""Concurrent admissions share configuration locks without admitting mutations."""

import pytest
from a13n_service.connectivity.connectors.connections import ConnectorConnectionService
from a13n_service.connectivity.connectors.models import ConnectorConnectionRecord, ConnectorProviderRecord
from a13n_service.connectivity.connectors.service import ConnectorProviderService
from a13n_service.connectivity.selection_domain import ConnectorConnectionToolSelection
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver
from a13n_service.storage import transaction
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from .conftest import NOW, ORG_ID, WORKSPACE_ID, actor
from .connector_helpers import FakeConnectorBackend, fake_registry
from .test_connector_service import create_connection, create_connector


@pytest.mark.anyio
async def test_connector_snapshot_readers_overlap_while_provider_and_account_edits_wait(
    postgres_connectivity_sessions, credential_protector
):
    sessions = postgres_connectivity_sessions
    registry = fake_registry(FakeConnectorBackend())
    providers = ConnectorProviderService(sessions, registry, credential_protector, clock=lambda: NOW)
    connections = ConnectorConnectionService(
        sessions,
        registry,
        credential_protector,
        correlation_secret=b"c" * 32,
        public_origin="https://foundation.example",
        setup_ttl_seconds=600,
        clock=lambda: NOW,
    )
    provider = await create_connector(providers)
    connection = await create_connection(connections, connector_provider_id=provider.id, idempotency_key="connection")
    launch = await connections.start_setup(
        actor=actor(),
        connection_id=connection.id,
        idempotency_key="setup",
        expected_version=connection.version,
        setup={"scopes": ["read"]},
        browser_nonce="b" * 64,
        return_path="/",
    )
    await connections.complete_callback(
        actor=actor(),
        attempt_id=launch.attempt_id,
        browser_nonce="b" * 64,
        session_uri=f"session://{launch.attempt_id}",
    )
    resolver = ConnectivitySelectionResolver(sessions)
    prepared = await resolver.prepare(
        actor=actor(),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_tools=(ConnectorConnectionToolSelection(connector_connection_id=connection.id),),
        mcp_tools=(),
    )
    async with transaction(sessions) as first:
        expected = await resolver.freeze(first, prepared=prepared)
        async with transaction(sessions) as second:
            await second.execute(text("SET LOCAL lock_timeout = '100ms'"))
            assert await resolver.freeze(second, prepared=prepared) == expected
            for model, identity in ((ConnectorConnectionRecord, connection.id), (ConnectorProviderRecord, provider.id)):
                with pytest.raises(OperationalError) as conflict:
                    async with transaction(sessions) as updating:
                        await updating.execute(text("SET LOCAL lock_timeout = '100ms'"))
                        await updating.scalar(select(model).where(model.id == identity).with_for_update())
                assert conflict.value.orig.sqlstate == "55P03"
