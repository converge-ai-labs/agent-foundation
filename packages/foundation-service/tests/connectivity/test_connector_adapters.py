from __future__ import annotations

import json

import httpx2
import pytest
from a13n_service.connectivity.connectors.adapters import ConnectorAdapterError, SetupContext
from a13n_service.connectivity.connectors.providers.composio import ComposioAdapter
from a13n_service.connectivity.connectors.providers.openconnector import OpenConnectorAdapter
from pydantic import ValidationError


class _AllowEndpoint:
    async def validate(self, endpoint: str, *, resolve_dns: bool = True) -> str:
        assert resolve_dns
        return endpoint.rstrip("/")


def _context(*, callback: bool = False) -> SetupContext:
    return SetupContext(
        attempt_id="csa_abcdef1234567890",
        generation=1,
        provider_key="github",
        external_user_correlation="usrh_opaque",
        callback_url=("https://foundation.example/connectivity/v1/connector-setup/callback" if callback else None),
    )


def _openconnector(http_client: httpx2.AsyncClient) -> OpenConnectorAdapter:
    return OpenConnectorAdapter(http_client, _AllowEndpoint(), response_max_bytes=1024 * 1024)


def _composio(http_client: httpx2.AsyncClient) -> ComposioAdapter:
    return ComposioAdapter(http_client, _AllowEndpoint(), response_max_bytes=1024 * 1024)


@pytest.mark.anyio
async def test_openconnector_setup_inspection_catalog_execution_and_revoke_are_exact() -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/initiate"):
            return httpx2.Response(
                200,
                json={
                    "connectionId": "conn_external",
                    "redirectUrl": "https://api.openconnector.dev/connect/private",
                },
            )
        if path.endswith("/connections/conn_external") and request.method == "GET":
            return httpx2.Response(
                200,
                json={
                    "id": "conn_external",
                    "providerSlug": "github",
                    "userId": "usrh_opaque",
                    "status": "active",
                    "disabled": False,
                    "authMethodKind": "oauth2",
                    "alias": "work",
                },
            )
        if path.endswith("/api/v1/tools"):
            return httpx2.Response(
                200,
                json={"items": [{"slug": "GITHUB_CREATE"}], "version": "20260903_01"},
            )
        if path.endswith("/api/v1/tools/GITHUB_CREATE"):
            return httpx2.Response(
                200,
                json={
                    "slug": "GITHUB_CREATE",
                    "description": "Create",
                    "inputParameters": {"type": "object"},
                },
            )
        if path.endswith("/execute"):
            return httpx2.Response(200, json={"successful": True, "status": 201, "data": {"id": 1}})
        if request.method == "DELETE":
            return httpx2.Response(204)
        raise AssertionError(f"unexpected request: {request.method} {path}")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http_client:
        adapter = _openconnector(http_client)
        config = adapter.validate_config(
            {
                "api_profile": "native_v1",
                "deployment": "cloud",
                "enabled_provider_slugs": ["github"],
            },
            config_version="openconnector_native_v1",
        )
        endpoint = adapter.normalize_endpoint("https://api.openconnector.dev/", connector_config=config)
        credentials = adapter.validate_credentials(
            {"api_key": "project-secret"},
            config_version="openconnector_native_v1",
        )
        setup = adapter.validate_setup(
            {"auth_config_id": "ac_github"},
            provider_key="github",
            connector_config=config,
            config_version="openconnector_native_v1",
        )
        started = await adapter.start_setup(
            endpoint=endpoint,
            connector_config=config,
            credentials=credentials,
            setup=setup,
            context=_context(),
        )
        inspection = await adapter.inspect_connection(
            endpoint=endpoint,
            connector_config=config,
            credentials=credentials,
            external_ref=started.external_ref,
            expected_provider_key="github",
            expected_external_user_correlation="usrh_opaque",
        )
        catalog = await adapter.list_tools(
            endpoint=endpoint,
            connector_config=config,
            credentials=credentials,
            external_ref=started.external_ref,
            provider_key="github",
            cursor=None,
        )
        outcome = await adapter.execute_tool(
            endpoint=endpoint,
            connector_config=config,
            credentials=credentials,
            external_ref=started.external_ref,
            tool_key="GITHUB_CREATE",
            provider_version=catalog.provider_version,
            arguments={"title": "safe"},
            request_id="op_1",
        )
        await adapter.revoke_connection(
            endpoint=endpoint,
            connector_config=config,
            credentials=credentials,
            external_ref=started.external_ref,
            operation_id="cop_1",
        )

    assert inspection.status == "ready"
    assert inspection.safe_metadata == {
        "auth_method_kind": "oauth2",
        "alias": "work",
        "created_at": None,
        "updated_at": None,
    }
    assert catalog.items[0].input_schema == {"type": "object"}
    assert outcome.kind == "succeeded"
    assert json.loads(requests[0].content) == {"authConfigId": "ac_github", "userId": "usrh_opaque"}
    assert json.loads(requests[-2].content) == {
        "arguments": {"title": "safe"},
        "connectedAccountId": "conn_external",
    }
    assert all(request.headers["x-api-key"] == "project-secret" for request in requests)


@pytest.mark.anyio
async def test_openconnector_rejects_substitution_and_reports_unknown_write() -> None:
    responses = iter(
        (
            httpx2.Response(
                200,
                json={
                    "id": "another",
                    "providerSlug": "github",
                    "userId": "usrh_opaque",
                    "status": "active",
                },
            ),
            httpx2.Response(503),
        )
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: next(responses))) as http_client:
        adapter = _openconnector(http_client)
        with pytest.raises(ConnectorAdapterError, match="connection_substitution"):
            await adapter.inspect_connection(
                endpoint="https://api.openconnector.dev",
                connector_config={},
                credentials={"api_key": "secret"},
                external_ref="expected",
                expected_provider_key="github",
                expected_external_user_correlation="usrh_opaque",
            )
        outcome = await adapter.execute_tool(
            endpoint="https://api.openconnector.dev",
            connector_config={},
            credentials={"api_key": "secret"},
            external_ref="expected",
            tool_key="GITHUB_CREATE",
            provider_version="v1",
            arguments={},
            request_id="op_unknown",
        )
    assert outcome.kind == "outcome_unknown"


@pytest.mark.anyio
async def test_composio_verified_callback_safe_projection_and_pinned_tool_version() -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/connected_accounts/link"):
            return httpx2.Response(
                200,
                json={
                    "connected_account_id": "ca_external",
                    "session_uri": "opaque-session",
                    "redirect_url": "https://backend.composio.dev/link/private",
                },
            )
        if path.endswith("/connected_accounts/complete_auth"):
            return httpx2.Response(200, json=_composio_account())
        if path.endswith("/api/v3.1/tools"):
            return httpx2.Response(
                200,
                json={
                    "items": [
                        {
                            "slug": "GITHUB_CREATE",
                            "description": "Create",
                            "input_parameters": {"type": "object"},
                        }
                    ],
                    "toolkit_version": "20260903_01",
                },
            )
        if path.endswith("/execute/GITHUB_CREATE"):
            return httpx2.Response(200, json={"successful": True, "data": {"id": 1}})
        if path.endswith("/revoke"):
            return httpx2.Response(200, json={"status": "REVOKED"})
        raise AssertionError(f"unexpected request: {request.method} {path}")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http_client:
        adapter = _composio(http_client)
        config = adapter.validate_config(
            {
                "connected_accounts_profile": "v3_1",
                "tools_profile": "v3_1",
                "enabled_toolkits": ["github"],
            },
            config_version="composio_v3_1",
        )
        setup = adapter.validate_setup(
            {"auth_config_id": "ac_github", "toolkit_version": "20260903_01"},
            provider_key="github",
            connector_config=config,
            config_version="composio_v3_1",
        )
        started = await adapter.start_setup(
            endpoint="https://backend.composio.dev",
            connector_config=config,
            credentials={"api_key": "secret"},
            setup=setup,
            context=_context(callback=True),
        )
        inspection = await adapter.complete_setup(
            endpoint="https://backend.composio.dev",
            connector_config=config,
            credentials={"api_key": "secret"},
            session_uri="opaque-session",
            context=_context(callback=True),
            expected_external_ref=started.external_ref,
        )
        catalog = await adapter.list_tools(
            endpoint="https://backend.composio.dev",
            connector_config=config,
            credentials={"api_key": "secret"},
            external_ref=started.external_ref,
            provider_key="github",
            cursor=None,
        )
        outcome = await adapter.execute_tool(
            endpoint="https://backend.composio.dev",
            connector_config=config,
            credentials={"api_key": "secret"},
            external_ref=started.external_ref,
            tool_key="GITHUB_CREATE",
            provider_version=catalog.provider_version,
            arguments={},
            request_id="op_2",
        )

    assert inspection.status == "ready"
    assert "state" not in inspection.safe_metadata
    assert "access_token" not in inspection.safe_metadata
    assert outcome.kind == "succeeded"
    execute_body = json.loads(requests[-1].content)
    assert execute_body["connected_account_id"] == "ca_external"
    assert execute_body["toolkit_version"] == "20260903_01"


def test_connector_setup_contracts_reject_provider_credentials_and_unpinned_versions() -> None:
    with pytest.raises(ValidationError):
        from a13n_service.connectivity.connectors.providers.openconnector import OpenConnectorSetup

        OpenConnectorSetup.model_validate({"auth_config_id": "ac", "credentials": {"token": "secret"}})
    with pytest.raises(ValidationError):
        from a13n_service.connectivity.connectors.providers.composio import ComposioSetup

        ComposioSetup.model_validate({"auth_config_id": "ac", "toolkit_version": "latest"})


def _composio_account() -> dict[str, object]:
    return {
        "id": "ca_external",
        "toolkit_slug": "github",
        "user_id": "usrh_opaque",
        "status": "ACTIVE",
        "display_name": "Work",
        "state": {"access_token": "masked"},
        "access_token": "masked",
    }
