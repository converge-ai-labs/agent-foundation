"""Managed OOMOL accounts use project authority, never personal aliases."""

import json

import httpx2
import pytest
from a13n_service.connectivity.connectors.contracts import ConnectionBinding, ConnectorProviderError, SetupContext
from a13n_service.connectivity.connectors.http import ConnectorHttpClient
from a13n_service.connectivity.connectors.providers import built_in_connector_provider_registry
from a13n_service.connectivity.connectors.providers.openconnector.project import (
    OpenConnectorProvider,
    ProjectConfiguration,
    ProjectCredentials,
)
from a13n_service.connectivity.connectors.tool_discovery import discover_tools

from .connector_helpers import allow_dispatch
from .test_openconnector_catalog import AllowEndpoint

pytestmark = pytest.mark.anyio


@pytest.fixture
def project_server():
    state = {"status": "initiated", "owner": "usrh_workspace", "account": "account-1", "lost": False}
    requests = []
    action = {
        "id": "github.get_user",
        "service": "github",
        "description": "Read user",
        "inputSchema": {"type": "object", "additionalProperties": False},
        "outputSchema": {"type": "object"},
    }

    def respond(request):
        requests.append(request)
        path = request.url.path
        project = path.startswith("/v1/saas/")
        assert request.headers["authorization"] == ("Bearer project-secret" if project else "Bearer catalog-secret")
        assert "x-oo-connector-alias" not in request.headers
        if path == "/v1/providers":
            data = [{"service": "github", "displayName": "GitHub", "authTypes": ["oauth2"]}]
        elif path == "/v1/actions":
            data = [action]
        elif path == "/v1/saas/connected-accounts/link":
            body = json.loads(request.content)
            assert set(body) == {"userId", "service", "alias"}
            assert body["service"] == "github" and body["userId"].startswith("usrh_")
            if state.get("use_request_owner"):
                state["owner"] = body["userId"]
            if state.get("lost_link"):
                raise httpx2.ReadTimeout("link response lost")
            data = {
                "id": "request-1",
                "service": "github",
                "externalUserId": state["owner"],
                "authorizationUrl": "https://github.com/login/oauth/authorize?state=opaque",
            }
        elif path == "/v1/saas/connection-requests/request-1":
            data = {
                "id": "request-1",
                "status": state["status"],
                "service": "github",
                "externalUserId": state["owner"],
                "connectedAccountId": state["account"],
                "authorizationUrl": "https://github.com/login/oauth/authorize?state=opaque",
            }
        elif path == "/v1/saas/connected-accounts/account-1/profile":
            data = {
                "connectedAccountId": state["account"],
                "service": "github",
                "externalUserId": state["owner"],
                "profile": {"displayName": "Team"},
            }
        elif path == "/v1/saas/actions/github.get_user":
            assert json.loads(request.content) == {
                "userId": "usrh_workspace",
                "service": "github",
                "connectedAccountId": "account-1",
                "input": {},
            }
            if state["lost"]:
                raise httpx2.ReadTimeout("lost response")
            data = {"executionId": "execution-1", "actionId": "github.get_user", "output": {"login": "team"}}
        else:
            raise AssertionError(path)
        return httpx2.Response(200, json={"success": True, "data": data})

    return state, requests, respond


def provider(http):
    return OpenConnectorProvider(
        ConnectorHttpClient(http, AllowEndpoint(), response_max_bytes=1024 * 1024),
        ProjectConfiguration(enabled_services=("github",)),
        ProjectCredentials(project_api_key="project-secret", catalog_api_key="catalog-secret"),
    )


def context():
    return SetupContext(
        attempt_id="setup_1", generation=1, connector_key="github", external_user_correlation="usrh_workspace"
    )


async def test_managed_setup_catalog_and_explicit_account_execution(project_server):
    state, requests, respond = project_server
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        runtime = provider(http)
        assert [item.key for item in await runtime.discover_connectors()] == ["github"]
        preview, version = await discover_tools(runtime.tool_catalog("github"))
        assert preview[0].key == "github.get_user"
        assert all("/saas/" not in request.url.path for request in requests)
        started = await runtime.start_setup(setup={}, context=context())
        assert started.setup_ref == "request-1" and started.external_ref is None
        assert await runtime.inspect_setup(setup_ref=started.setup_ref, context=context()) is None
        state["status"] = "connected"
        inspected = await runtime.inspect_setup(setup_ref=started.setup_ref, context=context())
        assert inspected.external_ref == "account-1"
        bound = runtime.connect(
            ConnectionBinding(
                external_ref=inspected.external_ref,
                connector_key="github",
                external_user_correlation=inspected.external_user_correlation,
            )
        )
        requests.clear()
        result = await bound.execute_tool(
            tool_key="github.get_user",
            provider_version=version,
            arguments={},
            request_id="call-1",
            before_dispatch=allow_dispatch,
        )
        assert result.result == {"login": "team"}
        assert not any("connection-requests" in request.url.path or "/apps" in request.url.path for request in requests)
        state["lost"] = True
        result = await bound.execute_tool(
            tool_key="github.get_user",
            provider_version=version,
            arguments={},
            request_id="call-2",
            before_dispatch=allow_dispatch,
        )
        assert result.kind == "outcome_unknown"
        assert sum(r.method == "POST" for r in requests) == 2


@pytest.mark.parametrize("change", ["owner", "account"])
async def test_managed_runtime_rejects_account_substitution(project_server, change):
    state, requests, respond = project_server
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        runtime = provider(http)
        _, version = await discover_tools(runtime.tool_catalog("github"))
        bound = runtime.connect(
            ConnectionBinding(
                external_ref="account-1", connector_key="github", external_user_correlation="usrh_workspace"
            )
        )
        state[change] = "other"
        with pytest.raises(ConnectorProviderError, match="connection_substitution"):
            await bound.execute_tool(
                tool_key="github.get_user",
                provider_version=version,
                arguments={},
                request_id="call",
                before_dispatch=allow_dispatch,
            )
        assert not any(r.method == "POST" for r in requests)


async def test_built_in_provider_catalog_registers_openconnector_with_private_credentials():
    async with httpx2.AsyncClient() as http:
        registry = built_in_connector_provider_registry(http, AllowEndpoint(), response_max_bytes=1024)
        definition = registry.require("openconnector")
        credentials = definition.credential_model.model_validate(
            {"project_api_key": "private1", "catalog_api_key": "private2"}
        )
        assert "private" not in repr(credentials)
        assert {item.type for item in registry.definitions()} == {"composio", "openconnector"}


@pytest.mark.parametrize("lost_link", [False, True])
async def test_registered_provider_managed_connection_lifecycle(
    project_server, connectivity_sessions, credential_protector, lost_link
):
    from a13n_service.connectivity.connectors.connections import ConnectorConnectionService
    from a13n_service.connectivity.connectors.domain import CreateConnectorProviderRequest
    from a13n_service.connectivity.connectors.errors import ConnectorError
    from a13n_service.connectivity.connectors.models import ConnectorConnectionRecord, ConnectorSetupAttemptRecord
    from a13n_service.connectivity.connectors.reconciler import ConnectorReconciler
    from a13n_service.connectivity.connectors.service import ConnectorProviderService
    from a13n_service.storage import transaction
    from sqlalchemy import select

    from .conftest import NOW, WORKSPACE_ID, actor
    from .test_connector_service import create_connection

    state, requests, respond = project_server
    state.update(use_request_owner=True, lost_link=lost_link)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        registry = built_in_connector_provider_registry(http, AllowEndpoint(), response_max_bytes=1024 * 1024)
        providers = ConnectorProviderService(connectivity_sessions, registry, credential_protector, clock=lambda: NOW)
        connections = ConnectorConnectionService(
            connectivity_sessions,
            registry,
            credential_protector,
            correlation_secret=b"c" * 32,
            public_origin=None,
            setup_ttl_seconds=600,
            clock=lambda: NOW,
        )
        created = await providers.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="provider",
            request=CreateConnectorProviderRequest(
                name="OpenConnector",
                type="openconnector",
                configuration={"enabled_services": ["github"]},
                credentials={"project_api_key": "project-secret", "catalog_api_key": "catalog-secret"},
            ),
        )
        tested = await providers.test(
            actor=actor(), connector_provider_id=created.id, expected_version=created.version, idempotency_key="test"
        )
        assert tested.verified_access == ("catalog_read",)
        assert (
            await providers.test(
                actor=actor(),
                connector_provider_id=created.id,
                expected_version=created.version,
                idempotency_key="test",
            )
            == tested
        )
        connection = await create_connection(
            connections, connector_provider_id=created.id, idempotency_key="connection"
        )
        launch_call = connections.start_setup(
            actor=actor(),
            connection_id=connection.id,
            expected_version=connection.version,
            idempotency_key="setup",
            setup={},
            return_path="/connections",
        )
        reconciler = ConnectorReconciler(
            connectivity_sessions,
            registry,
            connections.setup_coordinator,
            instance_id="control",
            poll_interval_seconds=2,
            lease_seconds=60,
            clock=lambda: NOW,
        )
        if lost_link:
            with pytest.raises(ConnectorError):
                await launch_call
            assert await reconciler.reconcile_once() is False
            async with transaction(connectivity_sessions) as session:
                attempt = await session.scalar(select(ConnectorSetupAttemptRecord))
                assert attempt.status == "failed"
            assert sum(r.url.path.endswith("/link") for r in requests) == 1
            return
        launch = await launch_call
        replay = await connections.start_setup(
            actor=actor(),
            connection_id=connection.id,
            expected_version=connection.version,
            idempotency_key="setup",
            setup={},
            return_path="/connections",
        )
        assert replay == launch
        assert sum(r.url.path.endswith("/link") for r in requests) == 1
        state["status"] = "connected"
        assert await reconciler.reconcile_once()
        async with transaction(connectivity_sessions) as session:
            record = await session.get(ConnectorConnectionRecord, connection.id)
            assert record.status == "ready"
            assert record.external_ref == "account-1" and record.external_user_correlation == state["owner"]
            version = record.version
            await session.delete(await session.get(ConnectorSetupAttemptRecord, launch.attempt_id))
        receipt = await connections.delete(
            actor=actor(), connection_id=connection.id, expected_version=version, idempotency_key="delete"
        )
        assert receipt.local_status == "deleted" and receipt.remote_status == "failed"


@pytest.mark.parametrize("keyword", ["allOf", "anyOf", "oneOf"])
async def test_composed_object_schemas_survive_mcp_discovery_and_native_validation(project_server, keyword):
    from jsonschema import Draft202012Validator

    _state, _requests, respond = project_server
    schema = {
        keyword: [{"type": "object", "properties": {"login": {"type": "string"}}, "required": ["login"]}],
        "description": "Native composition",
    }

    def composed(request):
        response = respond(request)
        if request.url.path == "/v1/actions":
            value = response.json()
            value["data"][0]["inputSchema"] = schema
            value["data"][0]["outputSchema"] = schema
            return httpx2.Response(200, json=value)
        return response

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(composed)) as http:
        runtime = provider(http)
        tools, version = await discover_tools(runtime.tool_catalog("github"))
        assert tools[0].input_schema == {**schema, "type": "object"}
        assert tools[0].output_schema == {**schema, "type": "object"}
        validator = Draft202012Validator(tools[0].input_schema)
        assert validator.is_valid({"login": "team"})
        assert not validator.is_valid({})
        assert not validator.is_valid({"login": 42})
        native = await runtime._catalog.actions("github")
        assert native[0].input_schema == schema
        assert native[0].output_schema == schema
        assert (await discover_tools(runtime.tool_catalog("github")))[1] == version


@pytest.mark.parametrize(
    "schema",
    [
        {"anyOf": [{"type": "object"}, {"type": "string"}]},
        {"allOf": [{"properties": {"login": {"type": "string"}}}]},
        {"allOf": [{"type": "object"}, {"$ref": "https://untrusted.example/schema"}]},
    ],
)
async def test_schema_projection_does_not_weaken_native_schema_safety(project_server, schema):
    _state, _requests, respond = project_server

    def incompatible(request):
        response = respond(request)
        if request.url.path == "/v1/actions":
            value = response.json()
            value["data"][0]["outputSchema"] = schema
            return httpx2.Response(200, json=value)
        return response

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(incompatible)) as http:
        with pytest.raises((ValueError, ConnectorProviderError)):
            await discover_tools(provider(http).tool_catalog("github"))
