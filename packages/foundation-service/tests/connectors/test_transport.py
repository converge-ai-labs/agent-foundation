from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from a13n_service.connectors import (
    ConnectorProvider,
    ConnectorProviderAccount,
    ConnectorProviderCapabilities,
    ConnectorProviderCatalog,
    ConnectorProviderConnection,
    ConnectorProviderConnectionResult,
    ConnectorProviderContext,
    ConnectorProviderMetadata,
    ConnectorProviderRegistration,
    ConnectorProviderSecret,
    ConnectorProviderSetupResult,
    ConnectorProviderTool,
    LocalConnectorProviderOperations,
    RemoteConnectorProviderOperations,
    create_connector_provider_operations_app,
)
from fastapi import FastAPI
from pydantic import SecretStr


class _ConnectionProvider(ConnectorProvider):
    @property
    def metadata(self) -> ConnectorProviderMetadata:
        return ConnectorProviderMetadata(
            display_name="Internal Test",
            description="Internal transport test Provider",
            contract_version="1",
            provider_config_schemas={
                "1": {
                    "type": "object",
                    "properties": {"tenant": {"type": "string"}},
                    "required": ["tenant"],
                    "additionalProperties": False,
                }
            },
            capabilities=ConnectorProviderCapabilities(tools=True, connections=True),
            connection_setup_modes=("manual",),
        )

    def validate_config(self, provider_config_version, config) -> None:
        if provider_config_version != "1":
            raise ValueError("unsupported")

    def connection_spec(self, provider_config_version, config):
        return {"type": "object", "required": ["api_key"]}

    async def list_tools(self, context, **values):
        return (
            ConnectorProviderTool(
                name="lookup",
                tool_id="internal.lookup",
                description="Look up a record",
                parameters_json_schema={"type": "object"},
                effects=("read",),
            ),
        )

    async def call_tool(self, context, **values):
        raise AssertionError("not used")

    async def start_connection(self, context, **values):
        assert values["input"] == {"api_key": "input-secret"}
        return ConnectorProviderSetupResult(
            completed=True,
            connection=ConnectorProviderConnectionResult(
                provider_state_version="1",
                provider_state={"tenant": values["config"]["tenant"]},
                account=ConnectorProviderAccount(external_id="acct-1", display_name="Account"),
                secrets=(ConnectorProviderSecret(key="access_token", value=SecretStr("provider-secret")),),
            ),
        )

    async def complete_connection(self, context, **values):
        raise AssertionError("not used")

    async def refresh_connection(self, context, **values):
        raise AssertionError("not used")

    async def revoke_connection(self, context, **values):
        return None

    def validate_connection(self, **values) -> None:
        connection: ConnectorProviderConnection = values["connection"]
        if connection.provider_state_version != "1":
            raise ValueError("incompatible")


def _catalog() -> ConnectorProviderCatalog:
    provider = _ConnectionProvider()
    registration = ConnectorProviderRegistration(
        provider_key="internal_test",
        class_module=__name__,
        class_qualname="_ConnectionProvider",
        import_target="tests:_ConnectionProvider",
        distribution_name="a13n-connector-internal-test",
        distribution_version="1.0.0",
        metadata=provider.metadata,
    )
    return ConnectorProviderCatalog(((registration, provider),))


@pytest.mark.anyio
async def test_authenticated_provider_operations_round_trip_credentials() -> None:
    token = SecretStr("internal-token-0123456789abcdef0123456789")
    internal = create_connector_provider_operations_app(
        LocalConnectorProviderOperations(_catalog()),
        authentication_token=token,
    )
    app = FastAPI()
    app.mount("/internal/connector-provider-operations", internal)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport,
        base_url="http://connector.test",
        headers={"Authorization": f"Bearer {token.get_secret_value()}"},
    ) as client:
        remote = RemoteConnectorProviderOperations(client)
        registrations = await remote.registrations()
        assert registrations[0].provider_key == "internal_test"
        assert (await remote.metadata("internal_test")).connection_setup_modes == ("manual",)
        await remote.validate_config("internal_test", provider_config_version="1", config={"tenant": "acme"})
        assert (
            await remote.connection_spec(
                "internal_test",
                provider_config_version="1",
                config={"tenant": "acme"},
            )
        )["required"] == ["api_key"]
        tools = await remote.list_tools(
            "internal_test",
            ConnectorProviderContext(
                operation_id="list-1",
                deadline=datetime.now(UTC) + timedelta(seconds=5),
            ),
            provider_config_version="1",
            config={"tenant": "acme"},
            connection=None,
        )
        assert tools[0].tool_id == "internal.lookup"
        result = await remote.start_connection(
            "internal_test",
            ConnectorProviderContext(
                operation_id="setup-1",
                deadline=datetime.now(UTC) + timedelta(seconds=5),
            ),
            provider_config_version="1",
            config={"tenant": "acme"},
            setup_mode="manual",
            input={"api_key": "input-secret"},
            callback_url=None,
            callback_state=None,
        )

    assert result.connection is not None
    assert result.connection.provider_state == {"tenant": "acme"}
    assert result.connection.secrets[0].value.get_secret_value() == "provider-secret"


@pytest.mark.anyio
async def test_provider_operations_reject_missing_internal_authentication() -> None:
    internal = create_connector_provider_operations_app(
        LocalConnectorProviderOperations(_catalog()),
        authentication_token=SecretStr("internal-token-0123456789abcdef0123456789"),
    )
    transport = httpx2.ASGITransport(app=internal)
    async with httpx2.AsyncClient(transport=transport, base_url="http://connector.test") as client:
        response = await client.post("/internal_test/metadata", json={})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.anyio
async def test_provider_operations_reject_declared_oversized_body_before_reading_it() -> None:
    token = SecretStr("internal-token-0123456789abcdef0123456789")
    internal = create_connector_provider_operations_app(
        LocalConnectorProviderOperations(_catalog()),
        authentication_token=token,
    )
    transport = httpx2.ASGITransport(app=internal)
    async with httpx2.AsyncClient(transport=transport, base_url="http://connector.test") as client:
        response = await client.post(
            "/internal_test/metadata",
            headers={
                "Authorization": f"Bearer {token.get_secret_value()}",
                "Content-Length": str(2 * 1024 * 1024 + 1),
            },
            content=b"{}",
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "invalid_request"
