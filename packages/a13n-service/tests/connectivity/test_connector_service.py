from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_service.connectivity.connectors.connections import ConnectorConnectionService
from a13n_service.connectivity.connectors.domain import (
    CreateConnectorConnectionRequest,
    CreateConnectorProviderRequest,
    ReplaceConnectorProviderCredentialsRequest,
    UpdateConnectorProviderRequest,
)
from a13n_service.connectivity.connectors.errors import ConnectorError
from a13n_service.connectivity.connectors.models import (
    ConnectorConnectionRecord,
    ConnectorSetupAttemptRecord,
)
from a13n_service.connectivity.connectors.reconciler import ConnectorReconciler
from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
from a13n_service.connectivity.connectors.service import ConnectorProviderService
from a13n_service.storage import transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, ORG_ID, WORKSPACE_ID, actor
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
    assert created.organization_id == ORG_ID
    assert created.configuration == {"tenant": "tenant-1", "endpoint": "https://connector.example"}
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
        browser_nonce="b" * 64,
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
        attempt_id=attempt.id,
        browser_nonce="b" * 64,
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
        browser_nonce="b" * 64,
        return_path="/settings/connectors",
    )
    assert replay.status == "completed"
    assert replay.redirect_url is None
    assert (
        await connections.complete_callback(
            actor=actor(), attempt_id=attempt.id, browser_nonce="b" * 64, session_uri=f"session://{attempt.id}"
        )
        == return_path
    )


@pytest.mark.anyio
async def test_local_delete_and_one_shot_remote_revoke(
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
        browser_nonce="b" * 64,
        return_path="/connections",
    )
    receipt = await connections.delete(
        actor=actor(), connection_id=connection.id, expected_version=launch.connection.version, idempotency_key="delete"
    )
    assert receipt.local_status == "deleted" and receipt.remote_status == "succeeded"
    assert len(connector_backend.revoked) == 1
    replay = await connections.delete(
        actor=actor(), connection_id=connection.id, expected_version=launch.connection.version, idempotency_key="delete"
    )
    assert replay == receipt and len(connector_backend.revoked) == 1
    with pytest.raises(ConnectorError):
        await connections.get(actor=actor(), connection_id=connection.id)


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
        browser_nonce="b" * 64,
        return_path="/connections",
    )
    registry = fake_registry(connector_backend)
    reconciler = ConnectorReconciler(
        connectivity_sessions,
        registry,
        connections.setup_coordinator,
        instance_id="reconciler-1",
        poll_interval_seconds=1,
        lease_seconds=60,
        clock=lambda: NOW,
    )

    assert await reconciler.reconcile_once() is True
    ready = await connections.get(actor=actor(), connection_id=connection.id)
    assert ready.status == "ready"


@pytest.mark.anyio
async def test_unknown_revoke_is_never_retried(
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
        browser_nonce="b" * 64,
        return_path="/connections",
    )
    await connections.complete_callback(
        actor=actor(),
        attempt_id=launch.attempt_id,
        browser_nonce="b" * 64,
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
    assert receipt.remote_status == "unknown" and receipt.local_status == "disabled"

    replay = await connections.revoke(
        actor=actor(), connection_id=connection.id, expected_version=ready.version, idempotency_key="unknown-revoke"
    )
    assert replay == receipt
    reconciler = ConnectorReconciler(
        connectivity_sessions,
        fake_registry(connector_backend),
        connections.setup_coordinator,
        instance_id="pod",
        poll_interval_seconds=1,
        lease_seconds=60,
        clock=lambda: NOW + timedelta(seconds=6),
    )
    assert await reconciler.reconcile_once() is False
    disabled = await connections.get(actor=actor(), connection_id=connection.id)
    assert disabled.status == "disabled"


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
@pytest.mark.parametrize("command", ["reconnect", "revoke"])
async def test_connection_http_commands_preserve_lifecycle_and_dispatch(
    connector_services, connector_backend, monkeypatch, command
) -> None:
    import httpx2
    from a13n_service.connectivity.connectors import router
    from a13n_service.iam import authenticate_request
    from fastapi import FastAPI

    providers, connections = connector_services
    provider = await create_connector(providers)
    connection = await create_connection(
        connections, connector_provider_id=provider.id, idempotency_key="http-connection"
    )
    from a13n_service.api import install_api_conventions

    app = FastAPI()
    install_api_conventions(app)
    app.include_router(router.router)
    app.dependency_overrides[authenticate_request] = actor
    monkeypatch.setattr(router, "_connections", lambda request: connections)
    path = f"/api/v1/connector-connections/{connection.id}"
    setup = {"setup": {"scopes": ["read"]}, "browser_nonce": "b" * 64, "return_path": "/connections"}
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app), base_url="https://foundation.example") as client:
        response = await client.post(
            path + "/setup",
            headers={"Idempotency-Key": "http-setup"},
            json={"expected_version": connection.version, **setup},
        )
        assert response.status_code == 200
        launch = response.json()
        duplicate = await client.post(
            path + "/setup",
            headers={"Idempotency-Key": "http-setup-another-key"},
            json={"expected_version": connection.version, **setup},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "setup_already_started"
        invalid_path = await client.post(
            path + "/setup",
            headers={"Idempotency-Key": "http-setup-invalid-path"},
            json={"expected_version": connection.version, **setup, "return_path": "//outside.invalid/"},
        )
        assert invalid_path.status_code == 400
        assert connector_backend.started == 1
        response = await client.post(
            "/api/v1/connector-setup/complete",
            json={
                "attempt_id": launch["attempt_id"],
                "browser_nonce": setup["browser_nonce"],
                "session_uri": f"session://{launch['attempt_id']}",
            },
        )
        assert response.status_code == 200
        current = (await client.get(path)).json()
        assert current["status"] == "ready"
        for index, (action, expected_status) in enumerate(
            (("disable", "disabled"), ("enable", "ready"), ("disable", "disabled"))
        ):
            response = await client.post(
                path + "/" + action,
                headers={"Idempotency-Key": f"{action}-{index}"},
                json={"expected_version": current["version"]},
            )
            assert response.status_code == 200 and response.headers.get("etag")
            current = response.json()
            assert current["status"] == expected_status

        body = {"expected_version": current["version"], **(setup if command == "reconnect" else {})}
        response = await client.post(path + "/" + command, headers={"Idempotency-Key": command}, json=body)
        assert response.status_code == 200
        result = response.json()
        if command == "reconnect":
            assert result["connection"]["id"] == connection.id
            assert result["connection"]["status"] == "pending"
            assert result["attempt_id"] != launch["attempt_id"]
            assert connector_backend.started == 2 and not connector_backend.revoked
        else:
            assert result["local_status"] == "disabled" and result["remote_status"] == "succeeded"
            assert (await client.get(path)).json()["status"] == "disabled"
            replay = await client.post(path + "/revoke", headers={"Idempotency-Key": command}, json=body)
            assert replay.status_code == 200 and replay.json() == result
            assert len(connector_backend.revoked) == 1


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
        browser_nonce="b" * 64,
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
        await connections.complete_callback(
            actor=actor(),
            attempt_id=launch.attempt_id,
            browser_nonce="b" * 64,
            session_uri=f"session://{launch.attempt_id}",
        )
    async with connectivity_sessions() as session:
        attempt = await session.get(ConnectorSetupAttemptRecord, launch.attempt_id)
        assert attempt is not None and attempt.status == "attached"
        assert attempt.reserved_at is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "rejection",
    [
        None,
        "scope_missing",
        "not_found",
        "rate_limited",
        "schema",
        "depth",
        "size",
        "error_unknown",
        "business_unknown",
    ],
)
async def test_worker_connector_uses_verified_binding_and_preserves_unknown_write(
    connector_services,
    connector_registry,
    connectivity_sessions,
    credential_protector,
    monkeypatch,
    external_runtime_factory,
    execution_authorization,
    rejection,
):
    from a13n_harness import AgentSpec, HarnessBuilder, HarnessInstrumentation, HarnessTraceContent
    from a13n_service.connectivity.connectors.contracts import ConnectorProviderError, ConnectorToolOutcome
    from a13n_service.connectivity.execution import AttemptToolScope
    from a13n_service.connectivity.mcp.transport import RemoteTransport
    from a13n_service.connectivity.selection_domain import ConnectorConnectionRunSelection
    from a13n_service.connectivity.selection_resolution import FrozenRunConnectivity
    from a13n_service.endpoint_policy import EndpointPolicy
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import StatusCode
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
        browser_nonce="b" * 64,
        return_path="/settings/connectors",
    )
    async with connectivity_sessions() as session:
        attempt = await session.scalar(select(ConnectorSetupAttemptRecord))
    await connections.complete_callback(
        actor=actor(), attempt_id=attempt.id, browser_nonce="b" * 64, session_uri=f"session://{attempt.id}"
    )
    calls = []
    guards = []

    async def execute(self, **kwargs):
        await kwargs["before_dispatch"]()
        calls.append((self.binding, kwargs))
        if rejection == "error_unknown":
            raise ConnectorProviderError("private_diagnostic", outcome_unknown=True)
        if rejection == "business_unknown":
            return ConnectorToolOutcome(kind="succeeded", result={"kind": "outcome_unknown", "ok": False})
        if rejection in {"schema", "depth", "size"}:
            payload = "invalid-object" if rejection == "schema" else {"value": "x" * (1024 * 1024)}
            if rejection == "depth":
                payload = {}
                for _ in range(70):
                    payload = {"nested": payload}
            return ConnectorToolOutcome(kind="succeeded", result=payload, request_id=kwargs["request_id"])
        if rejection is not None:
            raise ConnectorProviderError(
                "tool_rejected" if rejection == "not_found" else rejection,
                http_status=404 if rejection == "not_found" else None,
            )
        return ConnectorToolOutcome(kind="outcome_unknown", request_id=kwargs["request_id"])

    async def guard(session=None):
        guards.append(True)

    monkeypatch.setattr(FakeConnection, "execute_tool", execute)
    policy = EndpointPolicy()
    runtime = external_runtime_factory(connector_registry, RemoteTransport(policy), policy)
    capability = await runtime._connector(
        ConnectorConnectionRunSelection(
            connector_connection_id=connection.id, connector_provider_id=provider.id, tools=("issues.create",)
        ),
        guard,
        AttemptToolScope(
            replace(actor(), auth_method="internal"),
            ORG_ID,
            WORKSPACE_ID,
            FrozenRunConnectivity((), ()),
            (),
            authorization=await execution_authorization(),
        ),
    )
    tracer = TracerProvider()
    exporter = InMemorySpanExporter()
    tracer.add_span_processor(SimpleSpanProcessor(exporter))
    executable = HarnessBuilder(
        instrumentation=HarnessInstrumentation(tracer_provider=tracer, trace_content=HarnessTraceContent.NONE)
    ).build(AgentSpec(), output_type=str, model=TestModel(), capabilities=[capability])
    result = await executable.run("create issue")
    unknown = rejection in {None, "schema", "depth", "size", "error_unknown"}
    expected_text = "outcome_unknown" if unknown or rejection == "business_unknown" else rejection
    assert expected_text in result.output_or_raise()
    if not unknown and rejection != "business_unknown":
        assert '"failed"' in result.output_or_raise()
    tool = next(s for s in exporter.get_finished_spans() if s.attributes.get("gen_ai.operation.name") == "execute_tool")
    assert tool.attributes["a13n.tool.result.status"] == ("outcome_unknown" if unknown else "returned")
    assert tool.status.status_code is StatusCode.UNSET
    assert "private_diagnostic" not in repr(dict(tool.attributes))
    assert result.status == "completed"
    assert len(calls) == 1 and len(guards) == 3
    assert calls[0][0].external_ref == "external-1"
    assert calls[0][0].external_user_correlation == attempt.external_user_correlation
    assert calls[0][1]["provider_version"] == "fake-1"


@pytest.mark.anyio
async def test_org_provider_keeps_connections_and_external_correlation_in_workspace(
    connector_services, connectivity_sessions
):
    from dataclasses import replace

    from a13n_service.connectivity.connectors.domain import UpdateConnectorProviderRequest
    from a13n_service.storage import short_session

    from ..resource_scope_helpers import organization_admin, sibling_workspace

    providers, connections = connector_services
    admin = await organization_admin(connectivity_sessions, actor())
    sibling = await sibling_workspace(connectivity_sessions, admin)
    provider = await providers.create(
        actor=admin, workspace_id=None, idempotency_key="org-provider", request=connector_request()
    )
    assert provider.workspace_id is None
    assert (
        await providers.create(
            actor=admin, workspace_id=None, idempotency_key="org-provider", request=connector_request()
        )
        == provider
    )
    assert (await providers.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=50, cursor=None)).items == (provider,)
    assert (await providers.discover_connectors(actor=actor(), connector_provider_id=provider.id)).items[
        0
    ].key == "github"
    first = await create_connection(connections, connector_provider_id=provider.id, idempotency_key="first")
    second = await connections.create(
        actor=replace(actor(), boundary_workspace_id=sibling),
        workspace_id=sibling,
        idempotency_key="second",
        request=CreateConnectorConnectionRequest(
            connector_provider_id=provider.id,
            name="GitHub",
            connector_key="github",
        ),
    )
    assert first.workspace_id == WORKSPACE_ID
    assert second.workspace_id == sibling
    for connection, selected_actor in [(first, actor()), (second, replace(actor(), boundary_workspace_id=sibling))]:
        launch = await connections.start_setup(
            actor=selected_actor,
            connection_id=connection.id,
            idempotency_key="setup",
            expected_version=connection.version,
            setup={"scopes": ["read"]},
            browser_nonce="b" * 64,
            return_path="/connections",
        )
        await connections.complete_callback(
            actor=selected_actor,
            attempt_id=launch.attempt_id,
            browser_nonce="b" * 64,
            session_uri=f"session://{launch.attempt_id}",
        )
    async with short_session(connectivity_sessions) as session:
        attempts = tuple(
            await session.scalars(
                select(ConnectorSetupAttemptRecord).where(
                    ConnectorSetupAttemptRecord.connector_connection_id.in_([first.id, second.id])
                )
            )
        )
        assert len({item.external_user_correlation for item in attempts}) == 2
    with pytest.raises(ConnectorError):
        await providers.update(
            actor=actor(),
            connector_provider_id=provider.id,
            request=UpdateConnectorProviderRequest(name="Hijacked", expected_version=provider.version),
        )

    current = await connections.get(actor=actor(), connection_id=first.id)
    receipt = await connections.delete(
        actor=actor(),
        connection_id=first.id,
        expected_version=current.version,
        idempotency_key="delete-org-backed-connection",
    )
    assert receipt.local_status == "deleted" and receipt.remote_status == "succeeded"
    assert (
        await connections.get(actor=replace(actor(), boundary_workspace_id=sibling), connection_id=second.id)
    ).status == "ready"


async def test_disabled_connection_callback_cannot_restore_readiness(connector_services):
    providers, connections = connector_services
    provider = await create_connector(providers)
    connection = await create_connection(
        connections, connector_provider_id=provider.id, idempotency_key="late-callback"
    )
    launch = await connections.start_setup(
        actor=actor(),
        connection_id=connection.id,
        idempotency_key="setup",
        expected_version=connection.version,
        setup={"scopes": ["read"]},
        browser_nonce="b" * 64,
        return_path="/connections",
    )
    disabled = await connections.set_enabled(
        actor=actor(),
        connection_id=connection.id,
        expected_version=launch.connection.version,
        idempotency_key="disable",
        enabled=False,
    )
    with pytest.raises(ConnectorError):
        await connections.complete_callback(
            actor=actor(),
            attempt_id=launch.attempt_id,
            browser_nonce="b" * 64,
            session_uri=f"session://{launch.attempt_id}",
        )
    assert (await connections.get(actor=actor(), connection_id=connection.id)).status == "disabled"
    with pytest.raises(ConnectorError):
        await connections.set_enabled(
            actor=actor(),
            connection_id=connection.id,
            expected_version=disabled.version,
            idempotency_key="enable-incomplete",
            enabled=True,
        )


async def test_local_delete_survives_missing_remote_binding(
    connector_services, connector_backend, connectivity_sessions
):
    providers, connections = connector_services
    provider = await create_connector(providers)
    connection = await create_connection(
        connections, connector_provider_id=provider.id, idempotency_key="missing-binding"
    )
    launch = await connections.start_setup(
        actor=actor(),
        connection_id=connection.id,
        idempotency_key="setup",
        expected_version=connection.version,
        setup={"scopes": ["read"]},
        browser_nonce="b" * 64,
        return_path="/connections",
    )
    async with transaction(connectivity_sessions) as session:
        attempt = await session.get(ConnectorSetupAttemptRecord, launch.attempt_id)
        await session.delete(attempt)
        # Legacy partially attached connection whose attempt evidence was lost.
        record = await session.get(ConnectorConnectionRecord, connection.id)
        record.external_ref = "external-1"
    receipt = await connections.delete(
        actor=actor(),
        connection_id=connection.id,
        expected_version=launch.connection.version,
        idempotency_key="delete-missing-binding",
    )
    assert receipt.local_status == "deleted" and receipt.remote_status == "unknown"
    assert connector_backend.revoked == []


async def test_connection_listing_query_count_is_constant(connector_services, connectivity_sessions):
    from sqlalchemy import event

    providers, connections = connector_services
    provider = await create_connector(providers)
    engine = connectivity_sessions.kw["bind"].sync_engine
    counts = []
    for size in (1, 2, 3):
        await connections.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key=f"list-{size}",
            request=CreateConnectorConnectionRequest(
                connector_provider_id=provider.id, name=f"Connection {size}", connector_key="github"
            ),
        )
        statements = []

        def record(_connection, _cursor, statement, _parameters, _context, _executemany, *, recorded=statements):
            recorded.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            page = await connections.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=100, cursor=None)
            assert len(page.items) == size
        finally:
            event.remove(engine, "before_cursor_execute", record)
        counts.append(len(statements))
    assert counts[0] == counts[1] == counts[2]


@pytest.mark.parametrize("completion", ["callback", "polling", "after_inspection"])
async def test_setup_rechecks_initiator_management_authority(
    connector_services, connector_backend, connectivity_sessions, completion
):
    from a13n_service.iam.models import RoleBindingRecord

    providers, connections = connector_services
    provider = await create_connector(providers)
    connection = await create_connection(connections, connector_provider_id=provider.id, idempotency_key="role-change")
    launch = await connections.start_setup(
        actor=actor(),
        connection_id=connection.id,
        idempotency_key="setup",
        expected_version=connection.version,
        setup={"scopes": ["read"]},
        browser_nonce="b" * 64,
        return_path="/connections",
    )
    inspection = None
    if completion == "after_inspection":
        inspection = await connections.setup_coordinator._complete_attempt(
            launch.attempt_id,
            session_uri=f"session://{launch.attempt_id}",
        )
    async with transaction(connectivity_sessions) as session:
        binding = await session.get(RoleBindingRecord, "rb_connectivity_admin")
        binding.role_key = "viewer"
    with pytest.raises(ConnectorError):
        if completion == "callback":
            await connections.complete_callback(
                actor=actor(),
                attempt_id=launch.attempt_id,
                browser_nonce="b" * 64,
                session_uri=f"session://{launch.attempt_id}",
            )
        elif completion == "polling":
            await connections.setup_coordinator.attempt_snapshot(launch.attempt_id)
        else:
            await connections.setup_coordinator.finish_attempt(launch.attempt_id, inspection)
    async with transaction(connectivity_sessions) as session:
        record = await session.get(ConnectorConnectionRecord, connection.id)
        assert record.status == "pending"
        assert record.external_user_correlation is None


async def test_verified_binding_survives_setup_history_removal(connector_services, connectivity_sessions):
    from a13n_service.connectivity.connectors.connection_access import connection_binding

    providers, connections = connector_services
    provider = await create_connector(providers)
    connection = await create_connection(connections, connector_provider_id=provider.id, idempotency_key="independent")
    launch = await connections.start_setup(
        actor=actor(),
        connection_id=connection.id,
        idempotency_key="setup",
        expected_version=connection.version,
        setup={"scopes": ["read"]},
        browser_nonce="b" * 64,
        return_path="/connections",
    )
    await connections.complete_callback(
        actor=actor(),
        attempt_id=launch.attempt_id,
        browser_nonce="b" * 64,
        session_uri=f"session://{launch.attempt_id}",
    )
    async with transaction(connectivity_sessions) as session:
        record = await session.get(ConnectorConnectionRecord, connection.id)
        expected = connection_binding(record)
        version = record.version
        await session.delete(await session.get(ConnectorSetupAttemptRecord, launch.attempt_id))
    disabled = await connections.set_enabled(
        actor=actor(), connection_id=connection.id, expected_version=version, idempotency_key="disable", enabled=False
    )
    enabled = await connections.set_enabled(
        actor=actor(),
        connection_id=connection.id,
        expected_version=disabled.version,
        idempotency_key="enable",
        enabled=True,
    )
    assert enabled.status == "ready"
    async with transaction(connectivity_sessions) as session:
        assert connection_binding(await session.get(ConnectorConnectionRecord, connection.id)) == expected


async def test_provider_tool_preview_needs_no_connection(connector_services, connectivity_sessions):
    providers, _ = connector_services
    provider = await create_connector(providers)
    preview = await providers.preview_tools(actor=actor(), connector_provider_id=provider.id, connector_key="github")
    assert [tool.key for tool in preview.items] == ["issues.create"]
    async with transaction(connectivity_sessions) as session:
        assert await session.scalar(select(ConnectorConnectionRecord.id)) is None
        assert await session.scalar(select(ConnectorSetupAttemptRecord.id)) is None


async def test_expired_unattached_setup_requires_action_without_a_binding(
    connector_services, connector_registry, connectivity_sessions, monkeypatch
):
    from a13n_service.connectivity.connectors.contracts import ConnectorProviderError

    from .connector_helpers import FakeConnectorProvider

    async def unavailable(self, **kwargs):
        raise ConnectorProviderError("provider_unavailable", retryable=True)

    monkeypatch.setattr(FakeConnectorProvider, "start_setup", unavailable)
    providers, connections = connector_services
    provider = await create_connector(providers)
    connection = await create_connection(connections, connector_provider_id=provider.id, idempotency_key="expire")
    with pytest.raises(ConnectorError):
        await connections.start_setup(
            actor=actor(),
            connection_id=connection.id,
            expected_version=connection.version,
            idempotency_key="setup",
            setup={"scopes": ["read"]},
            browser_nonce="b" * 64,
            return_path="/connections",
        )
    reconciler = ConnectorReconciler(
        connectivity_sessions,
        connector_registry,
        connections.setup_coordinator,
        instance_id="expiry",
        poll_interval_seconds=2,
        lease_seconds=60,
        clock=lambda: NOW + timedelta(seconds=601),
    )
    assert await reconciler.reconcile_once()
    async with transaction(connectivity_sessions) as session:
        record = await session.get(ConnectorConnectionRecord, connection.id)
        assert record.status == "action_required" and record.external_ref is None
        assert record.external_user_correlation is None
        assert (await session.scalar(select(ConnectorSetupAttemptRecord))).status == "expired"


async def test_reconnect_cannot_reenable_a_previous_verified_generation(connector_services):
    providers, connections = connector_services
    provider = await create_connector(providers)
    connection = await create_connection(connections, connector_provider_id=provider.id, idempotency_key="reconnect")
    launch = await connections.start_setup(
        actor=actor(),
        connection_id=connection.id,
        expected_version=connection.version,
        idempotency_key="setup",
        setup={"scopes": ["read"]},
        browser_nonce="b" * 64,
        return_path="/connections",
    )
    await connections.complete_callback(
        actor=actor(),
        attempt_id=launch.attempt_id,
        browser_nonce="b" * 64,
        session_uri=f"session://{launch.attempt_id}",
    )
    ready = await connections.get(actor=actor(), connection_id=connection.id)
    ready = await connections.set_enabled(
        actor=actor(),
        connection_id=connection.id,
        expected_version=ready.version,
        idempotency_key="disable-before-reconnect",
        enabled=False,
    )
    reconnect = await connections.reconnect(
        actor=actor(),
        connection_id=connection.id,
        expected_version=ready.version,
        idempotency_key="reconnect",
        setup={"scopes": ["read"]},
        browser_nonce="b" * 64,
        return_path="/connections",
    )
    disabled = await connections.set_enabled(
        actor=actor(),
        connection_id=connection.id,
        expected_version=reconnect.connection.version,
        idempotency_key="disable",
        enabled=False,
    )
    with pytest.raises(ConnectorError, match="no verified setup"):
        await connections.set_enabled(
            actor=actor(),
            connection_id=connection.id,
            expected_version=disabled.version,
            idempotency_key="enable",
            enabled=True,
        )


@pytest.mark.anyio
async def test_provider_update_is_atomic(connector_services):
    providers, _ = connector_services
    original = await create_connector(providers)
    with pytest.raises(ConnectorError, match="invalid"):
        await providers.update(
            actor=actor(),
            connector_provider_id=original.id,
            request=UpdateConnectorProviderRequest(
                expected_version=original.version, name="Renamed", status="disabled", credentials={"api_key": "wrong"}
            ),
        )
    assert await providers.get(actor=actor(), connector_provider_id=original.id) == original
    updated = await providers.update(
        actor=actor(),
        connector_provider_id=original.id,
        request=UpdateConnectorProviderRequest(
            expected_version=original.version, name="Renamed", status="disabled", credentials={"api_key": "secret"}
        ),
    )
    assert updated.name == "Renamed" and updated.status == "disabled"
    assert updated.version == original.version + 1
    assert updated.credential_generation == original.credential_generation + 1
    with pytest.raises(ConnectorError):
        await providers.update(
            actor=actor(),
            connector_provider_id=original.id,
            request=UpdateConnectorProviderRequest(expected_version=original.version, name="Stale"),
        )
    assert await providers.get(actor=actor(), connector_provider_id=original.id) == updated
