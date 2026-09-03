from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from a13n_service.connectivity.composition import AdapterDefinition, AdapterRegistry
from a13n_service.connectivity.connectors.adapters import (
    AdapterConnectionStatus,
    AdapterStatusReason,
    ConnectionInspection,
    ConnectorAdapterError,
    ConnectorTool,
    ConnectorToolOutcome,
    ConnectorToolPage,
    SetupContext,
    SetupStarted,
)
from a13n_service.connectivity.connectors.catalog import ConnectorCatalogService, validate_catalog_tools
from a13n_service.connectivity.connectors.catalog_objects import ConnectorCatalogObjectStore
from a13n_service.connectivity.connectors.connections import ConnectorConnectionService
from a13n_service.connectivity.connectors.domain import (
    CreateConnectorConnectionRequest,
    CreateConnectorRequest,
    ReplaceConnectorCredentialsRequest,
)
from a13n_service.connectivity.connectors.errors import ConnectorError
from a13n_service.connectivity.connectors.models import (
    ConnectorConnectionRecord,
    ConnectorOperationRecord,
    ConnectorSetupAttemptRecord,
    ConnectorToolCatalogRecord,
)
from a13n_service.connectivity.connectors.reconciler import ConnectorReconciler
from a13n_service.connectivity.connectors.service import ConnectorService
from a13n_service.connectivity.ingress.domain import JsonObject
from a13n_service.iam import PrincipalRef
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, USER_ID, WORKSPACE_ID, actor


class FakeConnectorAdapter:
    driver_key = "fake_connector"
    config_versions = frozenset({"fake_v1"})

    def __init__(self) -> None:
        self.started = 0
        self.revoked: list[tuple[str, str]] = []
        self.inspection_status = AdapterConnectionStatus.ready
        self.fail_revoke = False
        self.supports_callback = True

    def validate_config(self, value: object, *, config_version: str) -> JsonObject:
        if config_version != "fake_v1" or value != {"tenant": "tenant-1"}:
            raise ValueError("invalid config")
        return {"tenant": "tenant-1"}

    def normalize_endpoint(self, value: str, *, connector_config: JsonObject) -> str:
        del connector_config
        if value != "https://connector.example":
            raise ValueError("invalid endpoint")
        return value

    def validate_credentials(self, value: dict[str, str], *, config_version: str) -> JsonObject:
        if config_version != "fake_v1" or value != {"api_key": "secret"}:
            raise ValueError("invalid credentials")
        return dict(value)

    def validate_setup(
        self,
        value: object,
        *,
        provider_key: str,
        connector_config: JsonObject,
        config_version: str,
    ) -> JsonObject:
        del connector_config
        if value != {"scopes": ["read"]} or provider_key != "github" or config_version != "fake_v1":
            raise ValueError("invalid setup")
        return {"scopes": ["read"]}

    async def test_connector(
        self,
        *,
        endpoint: str,
        connector_config: JsonObject,
        credentials: JsonObject,
    ) -> None:
        del endpoint, connector_config, credentials

    async def start_setup(
        self,
        *,
        endpoint: str,
        connector_config: JsonObject,
        credentials: JsonObject,
        setup: JsonObject,
        context: SetupContext,
    ) -> SetupStarted:
        del endpoint, connector_config, credentials, setup
        self.started += 1
        return SetupStarted(
            external_ref="external-1",
            redirect_url="https://connector.example/authorize",
            external_handle=f"session://{context.attempt_id}",
            supports_verified_callback=self.supports_callback,
        )

    async def complete_setup(
        self,
        *,
        endpoint: str,
        connector_config: JsonObject,
        credentials: JsonObject,
        session_uri: str,
        context: SetupContext,
        expected_external_ref: str,
    ) -> ConnectionInspection:
        del endpoint, connector_config, credentials, session_uri
        return ConnectionInspection(
            external_ref=expected_external_ref,
            provider_key=context.provider_key,
            external_user_correlation=context.external_user_correlation,
            status=AdapterConnectionStatus.ready,
            safe_metadata={"account": "safe"},
            provider_version="fake-1",
        )

    async def inspect_connection(
        self,
        *,
        endpoint: str,
        connector_config: JsonObject,
        credentials: JsonObject,
        external_ref: str,
        expected_provider_key: str,
        expected_external_user_correlation: str,
    ) -> ConnectionInspection:
        del endpoint, connector_config, credentials
        reason = (
            AdapterStatusReason.reauthorization_required
            if self.inspection_status is AdapterConnectionStatus.action_required
            else None
        )
        return ConnectionInspection(
            external_ref=external_ref,
            provider_key=expected_provider_key,
            external_user_correlation=expected_external_user_correlation,
            status=self.inspection_status,
            status_reason=reason,
            safe_metadata={"account": "safe"},
            provider_version="fake-1",
        )

    async def revoke_connection(
        self,
        *,
        endpoint: str,
        connector_config: JsonObject,
        credentials: JsonObject,
        external_ref: str,
        operation_id: str,
    ) -> None:
        del endpoint, connector_config, credentials
        self.revoked.append((external_ref, operation_id))
        if self.fail_revoke:
            raise ConnectorAdapterError("timeout", retryable=True, outcome_unknown=True)

    async def list_tools(self, **kwargs) -> ConnectorToolPage:
        del kwargs
        return ConnectorToolPage(
            items=(
                ConnectorTool(
                    key="issues.create",
                    description="Create an issue",
                    input_schema={"type": "object", "properties": {"title": {"type": "string"}}},
                    output_schema={"type": "object"},
                ),
            ),
            provider_version="fake-1",
        )

    async def execute_tool(self, **kwargs) -> ConnectorToolOutcome:
        raise NotImplementedError


@pytest.fixture
def connector_adapter() -> FakeConnectorAdapter:
    return FakeConnectorAdapter()


@pytest.fixture
def connector_registry(connector_adapter: FakeConnectorAdapter) -> AdapterRegistry[FakeConnectorAdapter]:
    return AdapterRegistry(
        (
            AdapterDefinition(
                key=connector_adapter.driver_key,
                config_versions=connector_adapter.config_versions,
                factory=lambda: connector_adapter,
            ),
        )
    )


@pytest.fixture
async def connector_services(
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_secrets,
    connector_registry,
) -> AsyncIterator[tuple[ConnectorService, ConnectorConnectionService]]:
    yield (
        ConnectorService(connectivity_sessions, connector_registry, connectivity_secrets, clock=lambda: NOW),
        ConnectorConnectionService(
            connectivity_sessions,
            connector_registry,
            connectivity_secrets,
            correlation_secret=b"c" * 32,
            public_origin="https://foundation.example",
            setup_ttl_seconds=600,
            clock=lambda: NOW,
        ),
    )


def connector_request() -> CreateConnectorRequest:
    return CreateConnectorRequest(
        name="Managed Connector",
        driver_key="fake_connector",
        config_version="fake_v1",
        endpoint="https://connector.example",
        config={"tenant": "tenant-1"},
        credentials={"api_key": "secret"},
    )


async def create_connector(service: ConnectorService):
    return await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-connector",
        request=connector_request(),
    )


async def create_connection(
    service: ConnectorConnectionService,
    *,
    connector_id: str,
    idempotency_key: str,
):
    return await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key=idempotency_key,
        request=CreateConnectorConnectionRequest(
            connector_id=connector_id,
            name="GitHub",
            provider_key="github",
            owner_principal_ref=PrincipalRef(principal_type="user", principal_id=USER_ID),
        ),
    )


@pytest.mark.anyio
async def test_connector_management_is_idempotent_and_keeps_credentials_private(
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
        connector_id=created.id,
        expected_version=created.version,
        idempotency_key="test-connector",
    )
    assert tested.status == "succeeded"
    assert (
        await connectors.test(
            actor=actor(),
            connector_id=created.id,
            expected_version=created.version,
            idempotency_key="test-connector",
        )
        == tested
    )
    with pytest.raises(ConnectorError) as invalid_rotation:
        await connectors.replace_credentials(
            actor=actor(),
            connector_id=created.id,
            idempotency_key="invalid-rotation",
            request=ReplaceConnectorCredentialsRequest(
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
    connector_adapter: FakeConnectorAdapter,
    connectivity_sessions: async_sessionmaker[AsyncSession],
) -> None:
    connectors, connections = connector_services
    connector = await create_connector(connectors)
    connection = await create_connection(
        connections,
        connector_id=connector.id,
        idempotency_key="create-connection",
    )
    assert connection.status == "pending"
    assert connector_adapter.started == 0
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
    assert connector_adapter.started == 1
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
    connector_adapter: FakeConnectorAdapter,
    connectivity_sessions: async_sessionmaker[AsyncSession],
) -> None:
    connectors, connections = connector_services
    connector = await create_connector(connectors)
    connection = await create_connection(
        connections,
        connector_id=connector.id,
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
    assert len(connector_adapter.revoked) == 1
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
async def test_ready_connection_catalog_is_validated_and_published_immutably(
    connector_services,
    connector_adapter: FakeConnectorAdapter,
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_secrets,
    connectivity_objects,
) -> None:
    connectors, connections = connector_services
    connector = await create_connector(connectors)
    connection = await create_connection(
        connections,
        connector_id=connector.id,
        idempotency_key="connection-for-catalog",
    )
    launch = await connections.start_setup(
        actor=actor(),
        connection_id=connection.id,
        idempotency_key="setup-for-catalog",
        expected_version=connection.version,
        setup={"scopes": ["read"]},
        return_path="/connections",
    )
    await connections.complete_callback(
        actor=actor(),
        session_uri=f"session://{launch.attempt_id}",
    )
    catalog = ConnectorCatalogService(
        connectivity_sessions,
        adapters=AdapterRegistry(
            (
                AdapterDefinition(
                    key=connector_adapter.driver_key,
                    config_versions=connector_adapter.config_versions,
                    factory=lambda: connector_adapter,
                ),
            )
        ),
        secrets=connectivity_secrets,
        objects=ConnectorCatalogObjectStore(connectivity_objects),
        clock=lambda: NOW,
    )
    digest = await catalog.refresh(connection.id)

    refreshed = await connections.get(actor=actor(), connection_id=connection.id)
    assert refreshed.catalog_digest == digest
    async with connectivity_sessions() as session:
        record = await session.scalar(select(ConnectorToolCatalogRecord))
    assert record is not None
    assert record.tool_count == 1
    assert record.connector_connection_id == connection.id

    await connectors.replace_credentials(
        actor=actor(),
        connector_id=connector.id,
        idempotency_key="rotate-catalog-credential",
        request=ReplaceConnectorCredentialsRequest(
            expected_version=connector.version,
            credentials={"api_key": "secret"},
        ),
    )
    invalidated = await connections.get(actor=actor(), connection_id=connection.id)
    assert invalidated.catalog_digest is None

    rotated_digest = await catalog.refresh(connection.id)
    assert rotated_digest != digest
    async with connectivity_sessions() as session:
        records = (
            await session.scalars(select(ConnectorToolCatalogRecord).order_by(ConnectorToolCatalogRecord.published_at))
        ).all()
    assert [item.connector_credential_generation for item in records] == [1, 2]


@pytest.mark.anyio
async def test_catalog_discovery_cannot_publish_after_its_lease_expires(
    connector_services,
    connector_adapter: FakeConnectorAdapter,
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_secrets,
    connectivity_objects,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connectors, connections = connector_services
    connector = await create_connector(connectors)
    connection = await create_connection(
        connections,
        connector_id=connector.id,
        idempotency_key="connection-for-expired-catalog",
    )
    launch = await connections.start_setup(
        actor=actor(),
        connection_id=connection.id,
        idempotency_key="setup-for-expired-catalog",
        expected_version=connection.version,
        setup={"scopes": ["read"]},
        return_path="/connections",
    )
    await connections.complete_callback(actor=actor(), session_uri=f"session://{launch.attempt_id}")
    clock = [NOW]
    original_list_tools = connector_adapter.list_tools

    async def expire_lease(**kwargs) -> ConnectorToolPage:
        clock[0] = NOW + timedelta(seconds=2)
        return await original_list_tools(**kwargs)

    monkeypatch.setattr(connector_adapter, "list_tools", expire_lease)
    catalog = ConnectorCatalogService(
        connectivity_sessions,
        adapters=AdapterRegistry(
            (
                AdapterDefinition(
                    key=connector_adapter.driver_key,
                    config_versions=connector_adapter.config_versions,
                    factory=lambda: connector_adapter,
                ),
            )
        ),
        secrets=connectivity_secrets,
        objects=ConnectorCatalogObjectStore(connectivity_objects),
        lease_seconds=1,
        clock=lambda: clock[0],
    )

    with pytest.raises(ConnectorError) as raised:
        await catalog.refresh(connection.id)
    assert raised.value.code == "catalog_lost_race"
    async with connectivity_sessions() as session:
        assert await session.scalar(select(ConnectorToolCatalogRecord)) is None


@pytest.mark.anyio
async def test_reconciler_completes_attached_setup_by_exact_external_reference(
    connector_services,
    connector_adapter: FakeConnectorAdapter,
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_secrets,
    connectivity_objects,
) -> None:
    connectors, connections = connector_services
    connector_adapter.supports_callback = False
    connector = await create_connector(connectors)
    connection = await create_connection(
        connections,
        connector_id=connector.id,
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
    registry = AdapterRegistry(
        (
            AdapterDefinition(
                key=connector_adapter.driver_key,
                config_versions=connector_adapter.config_versions,
                factory=lambda: connector_adapter,
            ),
        )
    )
    catalogs = ConnectorCatalogService(
        connectivity_sessions,
        registry,
        connectivity_secrets,
        ConnectorCatalogObjectStore(connectivity_objects),
        instance_id="reconciler-1",
        clock=lambda: NOW,
    )
    reconciler = ConnectorReconciler(
        connectivity_sessions,
        registry,
        connections.setup_coordinator,
        connections,
        catalogs,
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
    connector_adapter: FakeConnectorAdapter,
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_secrets,
    connectivity_objects,
) -> None:
    connectors, connections = connector_services
    connector = await create_connector(connectors)
    connection = await create_connection(
        connections,
        connector_id=connector.id,
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
    connector_adapter.fail_revoke = True
    receipt = await connections.revoke(
        actor=actor(),
        connection_id=connection.id,
        expected_version=ready.version,
        idempotency_key="unknown-revoke",
    )
    assert receipt.status == "unknown"

    connector_adapter.inspection_status = AdapterConnectionStatus.action_required
    registry = AdapterRegistry(
        (
            AdapterDefinition(
                key=connector_adapter.driver_key,
                config_versions=connector_adapter.config_versions,
                factory=lambda: connector_adapter,
            ),
        )
    )
    catalogs = ConnectorCatalogService(
        connectivity_sessions,
        registry,
        connectivity_secrets,
        ConnectorCatalogObjectStore(connectivity_objects),
        instance_id="reconciler-2",
        clock=lambda: NOW,
    )
    reconciler = ConnectorReconciler(
        connectivity_sessions,
        registry,
        connections.setup_coordinator,
        connections,
        catalogs,
        instance_id="reconciler-2",
        poll_interval_seconds=1,
        lease_seconds=60,
        clock=lambda: NOW + timedelta(seconds=6),
    )
    assert await reconciler.reconcile_once() is True
    async with connectivity_sessions() as session:
        operation = await session.get(ConnectorOperationRecord, receipt.operation_id)
    assert operation is not None and operation.status == "succeeded"


def test_catalog_rejects_invalid_schema_and_excessive_depth() -> None:
    with pytest.raises(ConnectorError) as invalid:
        validate_catalog_tools(
            [
                ConnectorTool(
                    key="invalid",
                    description="Invalid schema",
                    input_schema={"type": "not-a-json-schema-type"},
                )
            ]
        )
    assert invalid.value.code == "catalog_incompatible"

    nested: JsonObject = {}
    for _ in range(65):
        nested = {"nested": nested}
    with pytest.raises(ConnectorError) as deep:
        validate_catalog_tools([ConnectorTool(key="deep", description="Deep schema", input_schema=nested)])
    assert deep.value.code == "catalog_too_deep"
