"""Hosted app discovery does not require pre-created per-app auth configs."""

import json

import httpx2
import pytest
from a13n_harness.providers.connector.composio.catalog import (
    AuthConfiguration,
    ComposioCatalog,
    connector_metadata,
)
from a13n_harness.providers.connector.contracts import ConnectorProviderError
from a13n_harness.providers.connector.http import ConnectorHttpClient

from .test_connector_adapters import _AllowEndpoint as AllowEndpoint


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
    assert app.setup_schema["properties"]["auth_config_id"]["default"] == "create:OAUTH2"
    assert app.authentication_methods == ("OAUTH2",)
    assert connector_metadata(toolkit(slug="_1password"), ()).key == "_1password"
    assert app.logo_url == "https://logos.example/github.svg"
    assert connector_metadata(toolkit(no_auth=True), ()).unavailable_reason
    assert connector_metadata(
        toolkit(composio_managed_auth_schemes=[], auth_schemes=["API_KEY"]), ()
    ).authentication_methods == ("API_KEY",)


def test_explicit_configs_preserve_management_scopes_and_require_choice():
    app = connector_metadata(
        toolkit(),
        (
            AuthConfiguration("ac_one", "Personal", "github", True, "OAUTH2", scopes="read:user"),
            AuthConfiguration("ac_two", "Company", "github", False, "OAUTH2", scopes="repo"),
        ),
    )
    selection = app.setup_schema["properties"]["auth_config_id"]
    assert "default" not in selection
    assert [choice["const"] for choice in selection["oneOf"]] == ["ac_one", "ac_two"]
    assert "Composio managed" in selection["oneOf"][0]["title"]
    assert "Custom app" in selection["oneOf"][1]["title"]
    assert "repo" in selection["oneOf"][1]["title"]


def test_hosted_credentials_and_instance_fields_do_not_enter_local_schema():
    scheme = "API_KEY"
    app = connector_metadata(
        toolkit(
            auth_schemes=None,
            composio_managed_auth_schemes=[],
            auth_config_details=[
                {
                    "mode": scheme,
                    "fields": {
                        "auth_config_creation": {"required": []},
                        "connected_account_initiation": {
                            "required": [{"name": "api_key", "is_secret": True}, {"name": "subdomain"}]
                        },
                    },
                }
            ],
        ),
        (),
    )
    assert app.unavailable_reason is None
    assert app.authentication_methods == (scheme,)
    assert app.setup_schema["properties"]["auth_config_id"]["default"] == f"create:{scheme}"
    assert app.setup_schema["properties"]["connection_data"]["properties"] == {}


@pytest.mark.anyio
async def test_managed_configuration_creation_reuses_a_configuration_accepted_with_a_lost_response():
    configs = []
    posts = []

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
            await catalog.resolve_auth_config("github", "create:OAUTH2", configurations=await catalog.configurations())
        # Upstream accepted the POST despite a lost response; reuse its result.
        assert (
            await catalog.resolve_auth_config("github", "create:OAUTH2", configurations=await catalog.configurations())
        ).id == "ac_created"
    assert len(posts) == 1
    assert posts[0]["auth_config"]["type"] == "use_composio_managed_auth"


def test_hosted_composio_rejects_user_configuration():
    from a13n_harness.providers.connector.composio.configuration import ComposioConfiguration
    from pydantic import ValidationError

    assert ComposioConfiguration.model_json_schema()["properties"] == {}
    assert ComposioConfiguration.model_validate({}).model_dump() == {}
    with pytest.raises(ValidationError):
        ComposioConfiguration.model_validate({"endpoint": "custom"})


@pytest.mark.anyio
async def test_secretless_auth_configs_use_documented_scheme_and_one_catalog_read():
    scheme = "API_KEY"
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "GET" and "/toolkits/" in request.url.path:
            return httpx2.Response(
                200,
                json=toolkit(
                    auth_schemes=[scheme],
                    composio_managed_auth_schemes=[],
                    auth_config_details=[
                        {
                            "mode": scheme,
                            "fields": {
                                "auth_config_creation": {"required": []},
                                "connected_account_initiation": {"required": [{"name": "password", "is_secret": True}]},
                            },
                        }
                    ],
                ),
            )
        if request.method == "GET":
            return httpx2.Response(200, json={"items": []})
        body = json.loads(request.content)
        assert body["auth_config"]["authScheme"] == scheme
        assert body["auth_config"]["credentials"] == {}
        assert body["auth_config"]["type"] == "use_custom_auth"
        return httpx2.Response(
            200, json={"toolkit": {"slug": "github"}, "auth_config": {"id": "ac_new", "auth_scheme": scheme}}
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        catalog = ComposioCatalog(ConnectorHttpClient(http, AllowEndpoint(), response_max_bytes=65536), "secret")
        configured = await catalog.prepare_setup(
            "github", {"auth_config_id": f"create:{scheme}", "toolkit_version": "20260903_01"}
        )
    assert configured.scheme == scheme and configured.id == "ac_new"
    assert (
        len([request for request in requests if request.method == "GET" and request.url.path.endswith("auth_configs")])
        == 1
    )


@pytest.mark.anyio
async def test_ambiguous_managed_configs_are_never_arbitrarily_reused_or_created():
    configurations = (
        AuthConfiguration("ac_a", "Read", "github", True, "OAUTH2"),
        AuthConfiguration("ac_b", "Write", "github", True, "OAUTH2"),
    )
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: pytest.fail("Unexpected provider request"))
    ) as http:
        catalog = ComposioCatalog(ConnectorHttpClient(http, AllowEndpoint(), response_max_bytes=65536), "secret")
        with pytest.raises(ConnectorProviderError, match="auth_configuration_ambiguous"):
            await catalog.resolve_auth_config("github", "create:OAUTH2", configurations=configurations)
        assert (await catalog.resolve_auth_config("github", "ac_b", configurations=configurations)).id == "ac_b"


def test_app_credentials_require_dashboard_but_account_secrets_do_not():
    app = connector_metadata(
        toolkit(
            auth_schemes=["BASIC"],
            composio_managed_auth_schemes=[],
            auth_config_details=[
                {"mode": "BASIC", "fields": {"auth_config_creation": {"required": [{"name": "client_secret"}]}}}
            ],
        ),
        (),
    )
    assert app.unavailable_reason and "Composio Dashboard" in app.unavailable_reason


@pytest.mark.anyio
async def test_catalog_projects_scopes_without_app_or_account_credentials():
    config = {
        "id": "ac_custom",
        "name": "Custom",
        "toolkit": {"slug": "github"},
        "status": "ENABLED",
        "auth_scheme": "OAUTH2",
        "credentials": {
            "scopes": ["read:user", "repo"],
            "client_secret": "must-not-escape",
            "refresh_token": "also-secret",
        },
    }
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json={"items": [config]}))
    ) as http:
        catalog = ComposioCatalog(
            ConnectorHttpClient(http, AllowEndpoint(), response_max_bytes=65536), "provider-secret"
        )
        values = await catalog.configurations()
        encoded = connector_metadata(toolkit(), values).model_dump_json()
    assert "read:user, repo" in encoded
    assert not any(secret in encoded for secret in ("must-not-escape", "also-secret", "provider-secret"))


@pytest.mark.anyio
# OAuth differs from the other schemes, which share one code path.
@pytest.mark.parametrize("scheme", ["OAUTH2", "API_KEY"])
async def test_prefill_is_optional_typed_and_selected_scheme_specific_before_any_write(scheme):
    from jsonschema import Draft202012Validator

    other = "BASIC" if scheme != "BASIC" else "API_KEY"
    item = toolkit(
        auth_schemes=[scheme, other],
        auth_config_details=[
            {
                "mode": mode,
                "fields": {
                    "connected_account_initiation": {
                        "required": [
                            {"name": "subdomain", "type": "string", "is_secret": mode == other},
                            {"name": "password", "type": "string"},
                            {"name": "private_field", "type": "string", "is_secret": True},
                            {"name": "structured", "type": "object"},
                        ],
                        "optional": [{"name": "port", "type": "integer"}],
                    }
                },
            }
            for mode in (scheme, other)
        ],
    )
    configurations = (
        AuthConfiguration("ac_selected", "Selected", "github", True, scheme),
        AuthConfiguration("ac_other", "Other", "github", True, other),
    )
    validator = Draft202012Validator(connector_metadata(item, configurations).setup_schema)
    setup = {"auth_config_id": "ac_selected", "toolkit_version": "20260903_01"}
    assert validator.is_valid(setup)
    assert validator.is_valid({**setup, "connection_data": {"subdomain": "team", "port": 443}})
    assert not validator.is_valid({**setup, "auth_config_id": "ac_other", "connection_data": {"subdomain": "team"}})
    for prefill in (
        {"password": "secret"},
        {"private_field": "secret"},
        {"unknown": "x"},
        {"port": "443"},
        {"structured": {}},
        {"subdomain": None},
    ):
        assert not validator.is_valid({**setup, "connection_data": prefill})

    def respond(request):
        assert request.method == "GET", "Invalid prefill must fail before creating a shared auth config or link"
        return httpx2.Response(200, json=item if "/toolkits/" in request.url.path else {"items": []})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        catalog = ComposioCatalog(ConnectorHttpClient(http, AllowEndpoint(), response_max_bytes=65536), "secret")
        with pytest.raises(ConnectorProviderError, match="invalid_setup_options"):
            await catalog.prepare_setup(
                "github",
                {**setup, "auth_config_id": f"create:{scheme}", "connection_data": {"password": "secret"}},
            )
