"""Hosted app discovery does not require pre-created per-app auth configs."""

import json

import httpx2
import pytest
from a13n_service.connectivity.connectors.contracts import ConnectorProviderError
from a13n_service.connectivity.connectors.http import ConnectorHttpClient
from a13n_service.connectivity.connectors.providers.composio.catalog import (
    ComposioCatalog,
    connection_fields,
    connector_metadata,
)

from .connector_helpers import AllowEndpoint


def toolkit(**overrides):
    return {
        "slug": "github",
        "name": "GitHub",
        "meta": {"version": "20260903_01", "logo": "https://logos.example/github.svg"},
        "auth_schemes": ["OAUTH2"],
        "composio_managed_auth_schemes": ["OAUTH2"],
        **overrides,
    }


def test_managed_default_and_unsupported_auth_are_visible_before_setup():
    app = connector_metadata(toolkit(), ())
    assert app.setup_schema["properties"]["auth_config_id"]["default"] == "managed"
    assert app.authentication_methods == ("OAUTH2",)
    assert connector_metadata(toolkit(slug="_1password"), ()).key == "_1password"
    assert app.logo_url == "https://logos.example/github.svg"
    assert connector_metadata(toolkit(no_auth=True), ()).unavailable_reason
    assert (
        connector_metadata(
            toolkit(composio_managed_auth_schemes=[], auth_schemes=["API_KEY"]), ()
        ).authentication_methods
        == ()
    )


def test_connection_fields_preserve_nonsecret_requirements_and_reject_required_secrets():
    def fields(required, optional):
        return {
            "auth_config_details": [
                {
                    "mode": "OAUTH2",
                    "fields": {"connected_account_initiation": {"required": required, "optional": optional}},
                }
            ]
        }

    schema, reason = connection_fields(
        fields(
            [{"name": "subdomain", "type": "string"}], [{"name": "client_secret", "type": "string", "is_secret": True}]
        )
    )
    assert reason is None and schema["required"] == ["subdomain"]
    assert "client_secret" not in schema["properties"]
    schema, reason = connection_fields(fields([{"name": "api_key", "type": "string"}], []))
    assert schema is None and reason is not None


@pytest.mark.anyio
async def test_managed_configuration_creation_reconciles_before_single_use_gate():
    configs = []
    posts = []
    reserved = False

    async def reserve():
        nonlocal reserved
        if reserved:
            raise ConnectorProviderError("shared_setup_outcome_unknown")
        reserved = True

    def respond(request):
        if request.method == "GET":
            return httpx2.Response(200, json={"items": configs})
        posts.append(json.loads(request.content))
        configs.append(
            {
                "id": "ac_created",
                "name": "Managed",
                "toolkit": {"slug": "github"},
                "status": "ENABLED",
                "auth_scheme": "OAUTH2",
                "is_composio_managed": True,
            }
        )
        raise httpx2.ReadTimeout("response lost")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        catalog = ComposioCatalog(ConnectorHttpClient(http, AllowEndpoint(), response_max_bytes=65536), "secret")
        with pytest.raises(ConnectorProviderError):
            await catalog.resolve_auth_config("github", "managed", reserve)
        # Upstream accepted the POST despite a lost response; reuse its result.
        assert await catalog.resolve_auth_config("github", "managed", reserve) == "ac_created"
        configs.clear()
        with pytest.raises(ConnectorProviderError, match="shared_setup_outcome_unknown"):
            await catalog.resolve_auth_config("github", "managed", reserve)
    assert len(posts) == 1
    assert posts[0]["auth_config"]["type"] == "use_composio_managed_auth"


def test_hosted_composio_rejects_user_configuration():
    from a13n_service.connectivity.connectors.providers.composio.configuration import ComposioConfiguration
    from pydantic import ValidationError

    assert ComposioConfiguration.model_json_schema()["properties"] == {}
    assert ComposioConfiguration.model_validate({}).model_dump() == {}
    for field in ("endpoint", "project_identity", "connected_accounts_profile", "tools_profile"):
        with pytest.raises(ValidationError):
            ComposioConfiguration.model_validate({field: "custom"})
