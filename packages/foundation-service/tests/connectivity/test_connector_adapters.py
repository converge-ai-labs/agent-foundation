from __future__ import annotations

import json

import httpx2
import pytest
from a13n_service.connectivity.connectors.contracts import ConnectionBinding, ConnectorProviderError, SetupContext
from a13n_service.connectivity.connectors.http import ConnectorHttpClient
from a13n_service.connectivity.connectors.providers.composio import ComposioProvider
from a13n_service.connectivity.connectors.providers.composio.configuration import (
    ComposioConfiguration,
)
from a13n_service.connectivity.connectors.providers.configuration import ApiKeyCredentials
from pydantic import ValidationError

from .connector_helpers import allow_dispatch


class _AllowEndpoint:
    async def validate(self, endpoint: str, *, resolve_dns: bool = True) -> str:
        assert resolve_dns
        return endpoint.rstrip("/")


def _context(*, callback: bool = False) -> SetupContext:
    return SetupContext(
        attempt_id="csa_abcdef1234567890",
        generation=1,
        connector_key="github",
        external_user_correlation="usrh_opaque",
        callback_url=("https://foundation.example/connectivity/v1/connector-setup/callback" if callback else None),
    )


def _composio(http_client: httpx2.AsyncClient) -> ComposioProvider:
    return ComposioProvider(
        ConnectorHttpClient(http_client, _AllowEndpoint(), response_max_bytes=1024 * 1024),
        ComposioConfiguration(enabled_toolkits=("github",)),
        ApiKeyCredentials(api_key="secret"),
    )


@pytest.mark.anyio
async def test_composio_verified_callback_safe_projection_and_pinned_tool_version() -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/toolkits"):
            return httpx2.Response(
                200, json={"items": [{"slug": "github", "name": "GitHub", "meta": {"version": "20260903_01"}}]}
            )
        if path.endswith("/auth_configs"):
            return httpx2.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "ac_github",
                            "toolkit": {"slug": "github"},
                            "status": "ENABLED",
                            "auth_scheme": "OAUTH2",
                            "credentials": {"client_secret": "excluded"},
                        }
                    ]
                },
            )
        if path.endswith("/toolkits/github"):
            return httpx2.Response(200, json={"slug": "github", "meta": {"version": "20260903_01"}})
        if path.endswith("/tools/GITHUB_CREATE"):
            assert request.url.params["version"] == "20260903_01"
            return httpx2.Response(
                200,
                json={
                    "slug": "GITHUB_CREATE",
                    "toolkit": {"slug": "github"},
                    "version": "20260903_01",
                    "input_parameters": {"type": "object"},
                },
            )
        if path.endswith("/connected_accounts/link"):
            return httpx2.Response(
                200,
                json={
                    "connected_account_id": "ca_external",
                    "session_uri": "opaque-session",
                    "redirect_url": "https://backend.composio.dev/link/private",
                },
            )
        if path.endswith("/connected_accounts"):
            return httpx2.Response(200, json={"items": []})
        if path.endswith("/connected_accounts/complete_auth"):
            return httpx2.Response(200, json=_composio_account())
        if path.endswith("/api/v3.1/tools"):
            assert request.url.params["toolkit_versions[github]"] == "20260903_01"
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
        setup = {"auth_config_id": "ac_github", "toolkit_version": "20260903_01"}
        await adapter.test()
        started = await adapter.start_setup(
            setup=setup,
            context=_context(callback=True),
        )
        inspection = await adapter.complete_setup(
            session_uri="opaque-session",
            context=_context(callback=True),
            expected_external_ref=started.external_ref,
        )
        connection = adapter.connect(
            ConnectionBinding(
                external_user_correlation="usrh_opaque", external_ref=started.external_ref, connector_key="github"
            )
        )
        catalog = await connection.discover_tools(
            cursor=None,
        )
        outcome = await connection.execute_tool(
            tool_key="GITHUB_CREATE",
            provider_version=catalog.items[0].provider_version,
            arguments={},
            request_id="op_2",
            before_dispatch=allow_dispatch,
        )

    assert inspection.status == "ready"
    assert "state" not in inspection.safe_metadata
    assert "access_token" not in inspection.safe_metadata
    assert outcome.kind == "succeeded"
    execute_body = json.loads(requests[-1].content)
    assert execute_body["connected_account_id"] == "ca_external"
    assert execute_body["version"] == "20260903_01"


def test_connector_setup_contracts_reject_provider_credentials_and_unpinned_versions() -> None:
    with pytest.raises(ValidationError):
        from a13n_service.connectivity.connectors.providers.composio.configuration import ComposioSetup

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


@pytest.mark.anyio
async def test_same_type_accounts_have_independent_directories_and_bindings() -> None:
    from a13n_service.connectivity.connectors.discovery import validate_connectors
    from a13n_service.connectivity.connectors.providers import built_in_connector_provider_registry

    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path.endswith("/toolkits"):
            return httpx2.Response(
                200,
                json={
                    "items": [
                        {
                            "slug": "github",
                            "name": "GitHub",
                            "meta": {"version": "20260903_01"},
                            "credentials": "must not leak",
                        },
                        {"slug": "slack", "name": "Slack", "meta": {"version": "20260903_01"}},
                    ]
                },
            )
        if request.url.path.endswith("/auth_configs"):
            return httpx2.Response(
                200,
                json={
                    "items": [
                        {
                            "id": request.headers["x-api-key"] + "-config",
                            "toolkit": {"slug": "github"},
                            "status": "ENABLED",
                            "auth_scheme": "OAUTH2",
                            "credentials": {"secret": "must not leak"},
                        },
                        {
                            "id": "key-config",
                            "toolkit": {"slug": "slack"},
                            "status": "ENABLED",
                            "auth_scheme": "API_KEY",
                        },
                    ]
                },
            )
        raise AssertionError("construction, closing, and rejected setup must perform no remote mutation")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        registry = built_in_connector_provider_registry(http, _AllowEndpoint(), response_max_bytes=65536)
        definition = registry.require("composio")
        assert requests == []
        assert definition.definition().credential_schema["properties"]["api_key"]["writeOnly"] is True
        first = definition.configure({"enabled_toolkits": ["github", "slack"]}, {"api_key": "first"})
        second = definition.configure({"enabled_toolkits": ["github"]}, {"api_key": "second"})
        first_items = await first.discover_connectors()
        second_items = await second.discover_connectors()
        validate_connectors(first_items)
        assert first_items[0].setup_schema != second_items[0].setup_schema
        assert first_items[1].authentication_methods == ()
        assert len(second_items) == 1
        assert "must not leak" not in repr(first_items)
        with pytest.raises(ConnectorProviderError, match="invalid_setup_options"):
            await second.start_setup(
                setup={"auth_config_id": "first-config", "toolkit_version": "20260903_01"},
                context=_context(callback=True),
            )
        count = len(requests)
        connection = second.connect(
            ConnectionBinding(external_user_correlation="usrh_opaque", external_ref="verified", connector_key="github")
        )
        await connection.aclose()
        await second.aclose()
        await first.aclose()
        assert len(requests) == count
        assert not http.is_closed


@pytest.mark.anyio
async def test_discovery_rejects_repeated_pages_and_partial_failures() -> None:
    from a13n_service.connectivity.connectors.providers.discovery import DirectoryBudget, directory_items

    for responses in [
        [
            httpx2.Response(200, json={"items": [{}], "next_cursor": "same"}),
            httpx2.Response(200, json={"items": [{}], "next_cursor": "same"}),
        ],
        [httpx2.Response(200, json={"items": [{}], "next_cursor": "second"}), httpx2.Response(503)],
    ]:
        pages = iter(responses)
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _, pages=pages: next(pages))) as http:
            with pytest.raises(ConnectorProviderError):
                await directory_items(
                    ConnectorHttpClient(http, _AllowEndpoint(), response_max_bytes=65536),
                    endpoint="https://backend.composio.dev",
                    api_key="secret",
                    path="/api/v3.1/toolkits",
                    budget=DirectoryBudget(),
                )


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://malicious.example/schema"},
        {"type": "object", "properties": {"access_token": {"type": "string"}}},
        {"type": "object", "properties": {"option": {"type": "string", "writeOnly": True}}},
    ],
)
def test_discovery_rejects_unsafe_setup_schemas(schema) -> None:
    from a13n_service.connectivity.connectors.contracts import DiscoveredConnector
    from a13n_service.connectivity.connectors.discovery import validate_connectors

    with pytest.raises(ConnectorProviderError, match="unsafe_setup_schema"):
        validate_connectors(
            (DiscoveredConnector(key="github", name="GitHub", setup_schema=schema, authentication_methods=("oauth2",)),)
        )


@pytest.mark.anyio
@pytest.mark.parametrize("make_provider", [_composio])
async def test_malformed_setup_response_retains_unknown_outcome(make_provider, monkeypatch) -> None:
    from a13n_service.connectivity.connectors.contracts import DiscoveredConnector

    async def directory():
        return (
            DiscoveredConnector(
                key="github", name="GitHub", setup_schema={"type": "object"}, authentication_methods=("oauth2",)
            ),
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json={}))) as http:
        provider = make_provider(http)
        monkeypatch.setattr(provider, "discover_connectors", directory)
        setup = {"auth_config_id": "ac"}
        if make_provider is _composio:
            setup["toolkit_version"] = "20260903_01"
        with pytest.raises(ConnectorProviderError) as raised:
            await provider.start_setup(setup=setup, context=_context(callback=True))
        assert raised.value.outcome_unknown


@pytest.mark.anyio
async def test_directory_budget_covers_catalog_and_auth_config_reads(monkeypatch) -> None:
    from a13n_service.connectivity.connectors.providers import discovery

    monkeypatch.setattr(discovery, "DISCOVERY_MAX_TOOLS", 2)
    responses = iter(
        [
            httpx2.Response(200, json={"items": [{}], "total_items": 1}),
            httpx2.Response(200, json={"items": [{}, {}], "total_items": 2}),
        ]
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: next(responses))) as http:
        client = ConnectorHttpClient(http, _AllowEndpoint(), response_max_bytes=65536)
        budget = discovery.DirectoryBudget()
        await discovery.directory_items(
            client,
            endpoint="https://backend.composio.dev",
            api_key="secret",
            path="/toolkits",
            budget=budget,
        )
        with pytest.raises(ConnectorProviderError, match="directory_too_large"):
            await discovery.directory_items(
                client,
                endpoint="https://backend.composio.dev",
                api_key="secret",
                path="/auth_configs",
                budget=budget,
            )


@pytest.mark.anyio
async def test_malformed_completion_response_retains_unknown_outcome() -> None:
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json={}))) as http:
        with pytest.raises(ConnectorProviderError) as raised:
            await _composio(http).complete_setup(
                session_uri="session", context=_context(callback=True), expected_external_ref="account-1"
            )
        assert raised.value.outcome_unknown


async def test_composio_details_are_bounded_parallel_and_keep_catalog_order():
    from time import monotonic

    import anyio

    active = 0
    peak = 0
    names = [f"GITHUB_TOOL_{i}" for i in range(20)]

    async def respond(request):
        nonlocal active, peak
        path = request.url.path
        if path.endswith("/toolkits/github"):
            return httpx2.Response(200, json={"slug": "github", "meta": {"version": "20260903_01"}})
        if path.endswith("/tools"):
            return httpx2.Response(200, json={"items": [{"slug": name} for name in names]})
        key = path.rsplit("/", 1)[1]
        assert request.url.params["version"] == "20260903_01"
        active += 1
        peak = max(peak, active)
        try:
            await anyio.sleep(0.02)
            return httpx2.Response(
                200,
                json={
                    "slug": key,
                    "toolkit": {"slug": "github"},
                    "version": "20260903_01",
                    "input_parameters": {"type": "object"},
                },
            )
        finally:
            active -= 1

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        start = monotonic()
        page = await _composio(http).tool_catalog("github").discover_tools(cursor=None)
        elapsed = monotonic() - start
    assert 2 <= peak <= 8
    assert [item.key for item in page.items] == names
    print(
        f"20 tool details at 20ms simulated latency: {elapsed:.3f}s; peak concurrency={peak}; sequential floor=0.400s"
    )


async def test_connector_http_wall_deadline_preserves_unknown_write():
    import anyio

    class Trickle(httpx2.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(1000):
                await anyio.sleep(0.005)
                yield b" "

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, stream=Trickle()))
    ) as client:
        http = ConnectorHttpClient(client, _AllowEndpoint(), response_max_bytes=1024, timeout_seconds=0.03)
        with pytest.raises(ConnectorProviderError) as failure:
            await http.request(
                "POST", endpoint="https://provider.example", path="/effect", api_key="secret", write=True
            )
        assert failure.value.outcome_unknown and failure.value.retryable
