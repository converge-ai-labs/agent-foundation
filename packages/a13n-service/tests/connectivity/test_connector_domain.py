from datetime import UTC, datetime

import pytest
from a13n_service.connectivity.connections.domain import Connection
from a13n_service.iam.domain import PrincipalRef
from pydantic import ValidationError


def _connection(**changes: object) -> Connection:
    values: dict[str, object] = {
        "id": "cconn_abcdef1234567890",
        "organization_id": "org_abcdef1234567890",
        "workspace_id": "ws_abcdef1234567890",
        "source": {"kind": "connector", "provider_id": "cnr_abcdef1234567890", "connector_key": "github"},
        "authorization_generation": 1,
        "credential_configured": False,
        "name": "GitHub",
        "safe_metadata": {},
        "status": "pending",
        "status_reason": None,
        "version": 1,
        "created_by": PrincipalRef(principal_type="user", principal_id="usr_abcdef1234567890"),
        "created_at": datetime(2026, 9, 3, tzinfo=UTC),
        "updated_at": datetime(2026, 9, 3, tzinfo=UTC),
    }
    values.update(changes)
    return Connection.model_validate(values)


def test_connector_connection_status_reason_is_discriminated() -> None:
    assert _connection(status="action_required", status_reason="incompatible").status_reason == "incompatible"
    with pytest.raises(ValidationError, match="status_reason"):
        _connection(status="ready", status_reason="incompatible")
    with pytest.raises(ValidationError, match="status_reason"):
        _connection(status="action_required", status_reason=None)


def test_connector_connection_public_shape_excludes_external_reference() -> None:
    schema = Connection.model_json_schema()
    assert "external_ref" not in schema["properties"]


@pytest.mark.parametrize(
    "legacy",
    [
        {"driver_key": "composio"},
        {"config_version": "v3_1"},
        {"endpoint": "https://other.example"},
        {"config": {}},
    ],
)
def test_provider_creation_rejects_removed_fields(legacy) -> None:
    from a13n_service.connectivity.connectors.domain import CreateConnectorProviderRequest

    with pytest.raises(ValidationError, match="Extra inputs"):
        CreateConnectorProviderRequest.model_validate(
            {
                "name": "Work",
                "type": "composio",
                "configuration": {"deployment": "cloud", "enabled_provider_slugs": ["github"]},
                "credentials": {"api_key": "secret"},
                **legacy,
            }
        )


def test_model_provider_rejects_old_configuration_field() -> None:
    from a13n_service.models.domain import CreateModelProviderRequest

    with pytest.raises(ValidationError, match="Extra inputs"):
        CreateModelProviderRequest.model_validate(
            {"name": "Work", "type": "openai", "config": {}, "credential": "secret"}
        )


@pytest.mark.parametrize(
    "target",
    [
        "/",
        "//outside.invalid/",
        "http://outside.invalid",
        "http://localhost.example",
        "http://localhost:not-a-port/callback",
        "http://127.0.0.2",
        "http://[::2]",
        "https://user:password@outside.invalid",
        "https://outside.invalid/#fragment",
    ],
)
def test_authorization_requires_a_safe_browser_return_url(target):
    from a13n_service.connectivity.connections.domain import CreateAuthorizationRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CreateAuthorizationRequest(
            expected_version=1, method="browser", return_url=target, state="s" * 32, completion_challenge="a" * 64
        )


@pytest.mark.parametrize(
    "target",
    [
        "https://customer.example/oauth/complete",
        "http://localhost:5173/connections/callback",
        "http://127.0.0.1:5173/connections/callback",
        "http://[::1]:5173/connections/callback",
    ],
)
def test_authorization_accepts_https_or_exact_loopback_http_return_url(target):
    from a13n_service.connectivity.connections.domain import CreateAuthorizationRequest

    assert (
        CreateAuthorizationRequest(
            expected_version=1, method="browser", return_url=target, state="s" * 32, completion_challenge="a" * 64
        ).return_url
        == target
    )


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["enter", "body"])
async def test_provider_runtime_value_errors_are_not_configuration_errors(failure):
    from contextlib import asynccontextmanager
    from dataclasses import replace

    from a13n_harness.providers.connector import ConnectorProviderCatalog
    from a13n_harness.providers.connector.builtins import COMPOSIO
    from a13n_service.connectivity.connectors.composition import ConnectorProviders
    from a13n_service.connectivity.connectors.management import ProviderSnapshot, open_provider

    @asynccontextmanager
    async def native(configuration, credential, http):
        if failure == "enter":
            raise ValueError("native failure")
        yield object()

    providers = ConnectorProviders(ConnectorProviderCatalog((replace(COMPOSIO, open_provider=native),)))
    with pytest.raises(ValueError, match="native failure"):
        async with open_provider(providers, ProviderSnapshot("composio", {}, "active"), {"api_key": "secret"}):
            raise ValueError("native failure")
