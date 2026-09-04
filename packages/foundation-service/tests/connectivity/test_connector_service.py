from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from a13n_service.connectivity.connectors.connections import ConnectorConnectionService
from a13n_service.connectivity.connectors.contracts import (
    AdapterConnectionStatus,
)
from a13n_service.connectivity.connectors.domain import (
    CreateConnectorConnectionRequest,
    CreateConnectorProviderRequest,
    ReplaceConnectorProviderCredentialsRequest,
)
from a13n_service.connectivity.connectors.errors import ConnectorError
from a13n_service.connectivity.connectors.models import (
    ConnectorConnectionRecord,
    ConnectorOperationRecord,
    ConnectorSetupAttemptRecord,
)
from a13n_service.connectivity.connectors.reconciler import ConnectorReconciler
from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
from a13n_service.connectivity.connectors.service import ConnectorProviderService
from a13n_service.iam import PrincipalRef
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, USER_ID, WORKSPACE_ID, actor
from .connector_helpers import FakeConnectorBackend, fake_registry


@pytest.fixture
def connector_backend() -> FakeConnectorBackend:
    return FakeConnectorBackend()


@pytest.fixture
def connector_registry(connector_backend: FakeConnectorBackend) -> ConnectorProviderRegistry:
    return fake_registry(connector_backend)


@pytest.fixture
async def connector_services(
    connectivity_sessions: async_sessionmaker[AsyncSession],
    credential_protector,
    connector_registry,
) -> AsyncIterator[tuple[ConnectorProviderService, ConnectorConnectionService]]:
    yield (
        ConnectorProviderService(connectivity_sessions, connector_registry, credential_protector, clock=lambda: NOW),
        ConnectorConnectionService(
            connectivity_sessions,
            connector_registry,
            credential_protector,
            correlation_secret=b"c" * 32,
            public_origin="https://foundation.example",
            setup_ttl_seconds=600,
            clock=lambda: NOW,
        ),
    )


def connector_request() -> CreateConnectorProviderRequest:
    return CreateConnectorProviderRequest(
        name="Managed ConnectorProvider",
        type="fake_connector",
        configuration={"tenant": "tenant-1", "endpoint": "https://connector.example"},
        credentials={"api_key": "secret"},
    )


async def create_connector(service: ConnectorProviderService):
    return await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-connector",
        request=connector_request(),
    )


async def create_connection(
    service: ConnectorConnectionService,
    *,
    connector_provider_id: str,
    idempotency_key: str,
):
    return await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key=idempotency_key,
        request=CreateConnectorConnectionRequest(
            connector_provider_id=connector_provider_id,
            name="GitHub",
            connector_key="github",
            owner_principal_ref=PrincipalRef(principal_type="user", principal_id=USER_ID),
        ),
    )


@pytest.mark.anyio
async def test_connector_provider_management_is_idempotent_and_keeps_credentials_private(
    connector_services,
    connectivity_sessions: async_sessionmaker[AsyncSession],
) -> None:
    connectors, _connections = connector_services
    created = await create_connector(connectors)
    replay = await create_connector(connectors)

    assert replay == created
    assert created.credential_configured is True
    assert "secret" not in repr(created)
    tested = await connectors.test(
        actor=actor(),
        connector_provider_id=created.id,
        expected_version=created.version,
        idempotency_key="test-connector",
    )
    assert tested.status == "succeeded"
    assert (
        await connectors.test(
            actor=actor(),
            connector_provider_id=created.id,
            expected_version=created.version,
            idempotency_key="test-connector",
        )
        == tested
    )
    with pytest.raises(ConnectorError) as invalid_rotation:
        await connectors.replace_credentials(
            actor=actor(),
            connector_provider_id=created.id,
            idempotency_key="invalid-rotation",
            request=ReplaceConnectorProviderCredentialsRequest(
                expected_version=created.version,
                credentials={"api_key": "wrong"},
            ),
        )
    assert invalid_rotation.value.code == "invalid_credentials"

    async with connectivity_sessions() as session:
        record = await session.scalar(select(ConnectorConnectionRecord))
    assert record is None


@pytest.mark.anyio
async def test_connection_setup_is_durable_before_external_work_and_callback_is_single_use(
    connector_services,
    connector_backend: FakeConnectorBackend,
    connectivity_sessions: async_sessionmaker[AsyncSession],
) -> None:
    connectors, connections = connector_services
    connector = await create_connector(connectors)
    connection = await create_connection(
        connections,
        connector_provider_id=connector.id,
        idempotency_key="create-connection",
    )
    assert connection.status == "pending"
    assert connector_backend.started == 0
    launch = await connections.start_setup(
        actor=actor(),
        connection_id=connection.id,
        idempotency_key="start-setup",
        expected_version=connection.version,
        setup={"scopes": ["read"]},
        return_path="/settings/connectors",
    )
    assert launch.connection.status == "pending"
    assert launch.redirect_url == "https://connector.example/authorize"
    assert connector_backend.started == 1
    assert "external-1" not in repr(launch.connection)

    async with connectivity_sessions() as session:
        attempt = await session.scalar(select(ConnectorSetupAttemptRecord))
    assert attempt is not None
    return_path = await connections.complete_callback(
        actor=actor(),
        session_uri=f"session://{attempt.id}",
    )
    assert return_path == "/settings/connectors"
    ready = await connections.get(actor=actor(), connection_id=launch.connection.id)
    assert ready.status == "ready"
    assert ready.safe_metadata == {"account": "safe"}
    replay = await connections.start_setup(
        actor=actor(),
        connection_id=connection.id,
        idempotency_key="start-setup",
        expected_version=connection.version,
        setup={"scopes": ["read"]},
        return_path="/settings/connectors",
    )
    assert replay.status == "completed"
    assert replay.redirect_url is None
    with pytest.raises(ConnectorError) as replayed:
        await connections.complete_callback(actor=actor(), session_uri=f"session://{attempt.id}")
    assert replayed.value.code == "invalid_callback"


@pytest.mark.anyio
async def test_connection_revoke_precedes_tombstone(
    connector_services,
    connector_backend: FakeConnectorBackend,
    connectivity_sessions: async_sessionmaker[AsyncSession],
) -> None:
    connectors, connections = connector_services
    connector = await create_connector(connectors)
    connection = await create_connection(
        connections,
        connector_provider_id=connector.id,
        idempotency_key="connection-for-revoke",
    )
    launch = await connections.start_setup(
        actor=actor(),
        connection_id=connection.id,
        idempotency_key="setup-for-revoke",
        expected_version=connection.version,
        setup={"scopes": ["read"]},
        return_path="/connections",
    )
    with pytest.raises(ConnectorError) as premature:
        await connections.delete(
            actor=actor(),
            connection_id=launch.connection.id,
            expected_version=launch.connection.version,
            idempotency_key="premature-delete",
        )
    assert premature.value.code == "revocation_required"

    revoked = await connections.revoke(
        actor=actor(),
        connection_id=launch.connection.id,
        expected_version=launch.connection.version,
        idempotency_key="revoke",
    )
    assert revoked.status == "succeeded"
    assert revoked.connection.status == "action_required"
    assert revoked.connection.status_reason == "reauthorization_required"
    assert len(connector_backend.revoked) == 1
    await connections.delete(
        actor=actor(),
        connection_id=revoked.connection.id,
        expected_version=revoked.connection.version,
        idempotency_key="delete",
    )
    with pytest.raises(ConnectorError) as deleted:
        await connections.get(actor=actor(), connection_id=revoked.connection.id)
    assert deleted.value.code == "resource_not_found"

    async with connectivity_sessions() as session:
        operation = await session.scalar(select(ConnectorOperationRecord))
    assert operation is not None and operation.status == "succeeded"


@pytest.mark.anyio
async def test_reconciler_completes_attached_setup_by_exact_external_reference(
    connector_services,
    connector_backend: FakeConnectorBackend,
    connectivity_sessions: async_sessionmaker[AsyncSession],
    credential_protector,
    connectivity_objects,
) -> None:
    connectors, connections = connector_services
    connector_backend.supports_callback = False
    connector = await create_connector(connectors)
    connection = await create_connection(
        connections,
        connector_provider_id=connector.id,
        idempotency_key="connection-for-reconcile",
    )
    await connections.start_setup(
        actor=actor(),
        connection_id=connection.id,
        idempotency_key="setup-for-reconcile",
        expected_version=connection.version,
        setup={"scopes": ["read"]},
        return_path="/connections",
    )
    registry = fake_registry(connector_backend)
    reconciler = ConnectorReconciler(
        connectivity_sessions,
        registry,
        connections.setup_coordinator,
        connections,
        instance_id="reconciler-1",
        poll_interval_seconds=1,
        lease_seconds=60,
        clock=lambda: NOW,
    )

    assert await reconciler.reconcile_once() is True
    ready = await connections.get(actor=actor(), connection_id=connection.id)
    assert ready.status == "ready"


@pytest.mark.anyio
async def test_unknown_revoke_reconciles_only_from_same_external_reference(
    connector_services,
    connector_backend: FakeConnectorBackend,
    connectivity_sessions: async_sessionmaker[AsyncSession],
    credential_protector,
    connectivity_objects,
) -> None:
    connectors, connections = connector_services
    connector = await create_connector(connectors)
    connection = await create_connection(
        connections,
        connector_provider_id=connector.id,
        idempotency_key="connection-for-unknown-revoke",
    )
    launch = await connections.start_setup(
        actor=actor(),
        connection_id=connection.id,
        idempotency_key="setup-for-unknown-revoke",
        expected_version=connection.version,
        setup={"scopes": ["read"]},
        return_path="/connections",
    )
    await connections.complete_callback(
        actor=actor(),
        session_uri=f"session://{launch.attempt_id}",
    )
    ready = await connections.get(actor=actor(), connection_id=connection.id)
    connector_backend.fail_revoke = True
    receipt = await connections.revoke(
        actor=actor(),
        connection_id=connection.id,
        expected_version=ready.version,
        idempotency_key="unknown-revoke",
    )
    assert receipt.status == "unknown"

    connector_backend.inspection_status = AdapterConnectionStatus.action_required
    registry = fake_registry(connector_backend)
    reconciler = ConnectorReconciler(
        connectivity_sessions,
        registry,
        connections.setup_coordinator,
        connections,
        instance_id="reconciler-2",
        poll_interval_seconds=1,
        lease_seconds=60,
        clock=lambda: NOW + timedelta(seconds=6),
    )
    assert await reconciler.reconcile_once() is True
    async with connectivity_sessions() as session:
        operation = await session.get(ConnectorOperationRecord, receipt.operation_id)
    assert operation is not None and operation.status == "succeeded"


@pytest.mark.anyio
async def test_provider_metadata_and_discovery_use_new_routes_without_creating_connections(
    connector_services, connectivity_sessions, monkeypatch
) -> None:
    import httpx2
    from a13n_service.connectivity.connectors import router
    from a13n_service.iam import authenticate_request
    from fastapi import FastAPI

    providers, _ = connector_services
    provider = await create_connector(providers)
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[authenticate_request] = actor
    monkeypatch.setattr(router, "_connector_providers", lambda request: providers)
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app), base_url="https://foundation.example") as client:
        definitions = await client.get("/api/v1/connector-provider-types")
        assert definitions.status_code == 200
        definition = definitions.json()["items"][0]
        assert definition["type"] == "fake_connector"
        assert "endpoint" in definition["configuration_schema"]["properties"]
        response = await client.post(f"/api/v1/connector-providers/{provider.id}/discover-connectors")
        assert response.status_code == 200
        assert response.json()["items"][0]["connector_provider_id"] == provider.id
        assert (await client.get(f"/api/v1/connectors/{provider.id}")).status_code == 404
    async with connectivity_sessions() as session:
        assert await session.scalar(select(ConnectorConnectionRecord)) is None
        assert await session.scalar(select(ConnectorSetupAttemptRecord)) is None
    schema = app.openapi()
    assert "ConnectorProvider" in schema["components"]["schemas"]
    properties = schema["components"]["schemas"]["CreateConnectorProviderRequest"]["properties"]
    assert {"type", "configuration", "credentials"} <= properties.keys()
    assert {"driver_key", "endpoint", "config", "config_version"}.isdisjoint(properties)


@pytest.mark.anyio
async def test_connector_discovery_fences_credential_rotation(connector_services, monkeypatch) -> None:
    from .connector_helpers import FakeConnectorProvider

    providers, _ = connector_services
    provider = await create_connector(providers)
    original = FakeConnectorProvider.discover_connectors

    async def rotate_during_discovery(runtime):
        await providers.replace_credentials(
            actor=actor(),
            connector_provider_id=provider.id,
            idempotency_key="discovery-rotation",
            request=ReplaceConnectorProviderCredentialsRequest(
                expected_version=provider.version, credentials={"api_key": "secret"}
            ),
        )
        return await original(runtime)

    monkeypatch.setattr(FakeConnectorProvider, "discover_connectors", rotate_during_discovery)
    with pytest.raises(ConnectorError) as raised:
        await providers.discover_connectors(actor=actor(), connector_provider_id=provider.id)
    assert raised.value.code == "connector_provider_changed"


@pytest.mark.anyio
async def test_disabled_provider_blocks_discovery(connector_services) -> None:
    from a13n_service.connectivity.connectors.domain import ConnectorProviderStatus

    providers, _ = connector_services
    provider = await create_connector(providers)
    await providers.set_status(
        actor=actor(),
        connector_provider_id=provider.id,
        status=ConnectorProviderStatus.disabled,
        expected_version=provider.version,
        idempotency_key="disable-discovery",
    )
    with pytest.raises(ConnectorError) as raised:
        await providers.discover_connectors(actor=actor(), connector_provider_id=provider.id)
    assert raised.value.code == "connector_provider_disabled"


@pytest.mark.anyio
async def test_disabled_provider_stops_setup_and_releases_callback_reservation(
    connector_services, connector_backend, connectivity_sessions
) -> None:
    from a13n_service.connectivity.connectors.domain import ConnectorProviderStatus

    providers, connections = connector_services
    provider = await create_connector(providers)
    connection = await create_connection(
        connections, connector_provider_id=provider.id, idempotency_key="disabled-setup-connection"
    )
    launch = await connections.start_setup(
        actor=actor(),
        connection_id=connection.id,
        idempotency_key="disabled-setup",
        expected_version=connection.version,
        setup={"scopes": ["read"]},
        return_path="/connections",
    )
    await providers.set_status(
        actor=actor(),
        connector_provider_id=provider.id,
        status=ConnectorProviderStatus.disabled,
        expected_version=provider.version,
        idempotency_key="disable-before-callback",
    )
    with pytest.raises(ConnectorError) as raised:
        await connections.setup_coordinator.start_attempt(launch.attempt_id)
    assert raised.value.code == "connector_provider_disabled"
    assert connector_backend.started == 1
    with pytest.raises(ConnectorError):
        await connections.complete_callback(actor=actor(), session_uri=f"session://{launch.attempt_id}")
    async with connectivity_sessions() as session:
        attempt = await session.get(ConnectorSetupAttemptRecord, launch.attempt_id)
        assert attempt is not None and attempt.status == "attached"
        assert attempt.reserved_at is None


@pytest.mark.anyio
async def test_worker_connector_uses_verified_binding_and_preserves_unknown_write(
    connector_services,
    connector_registry,
    connectivity_sessions,
    credential_protector,
    monkeypatch,
):
    from a13n_service.connectivity.connectors.contracts import ConnectorToolOutcome
    from a13n_service.connectivity.execution import ExternalToolRuntime
    from a13n_service.connectivity.mcp.transport import RemoteTransport
    from a13n_service.connectivity.selection_domain import ConnectorConnectionRunSelection
    from a13n_service.endpoint_policy import EndpointPolicy
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    from .connector_helpers import FakeConnection

    providers, connections = connector_services
    provider = await create_connector(providers)
    connection = await create_connection(
        connections, connector_provider_id=provider.id, idempotency_key="runtime-account"
    )
    await connections.start_setup(
        actor=actor(),
        connection_id=connection.id,
        idempotency_key="runtime-setup",
        expected_version=connection.version,
        setup={"scopes": ["read"]},
        return_path="/settings/connectors",
    )
    async with connectivity_sessions() as session:
        attempt = await session.scalar(select(ConnectorSetupAttemptRecord))
    await connections.complete_callback(actor=actor(), session_uri=f"session://{attempt.id}")
    calls = []
    guards = []

    async def execute(self, **kwargs):
        calls.append((self.binding, kwargs))
        return ConnectorToolOutcome(kind="outcome_unknown", request_id=kwargs["request_id"])

    async def guard():
        guards.append(True)

    monkeypatch.setattr(FakeConnection, "execute_tool", execute)
    policy = EndpointPolicy()
    runtime = ExternalToolRuntime(
        connectivity_sessions, credential_protector, connector_registry, RemoteTransport(policy), policy
    )
    capability = await runtime._connector(
        ConnectorConnectionRunSelection(
            connector_connection_id=connection.id, connector_provider_id=provider.id, tools=("issues.create",)
        ),
        guard,
    )
    result = await Agent(TestModel(), capabilities=[capability]).run("create issue")
    assert "outcome_unknown" in result.output
    assert len(calls) == 1 and len(guards) >= 3
    assert calls[0][0].external_ref == "external-1"
    assert calls[0][0].external_user_correlation == attempt.external_user_correlation
    assert calls[0][1]["provider_version"] == "fake-1"
