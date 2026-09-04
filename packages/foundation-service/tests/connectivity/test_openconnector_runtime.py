from __future__ import annotations

import json

import httpx2
import pytest
from a13n_service.connectivity.connectors.contracts import ConnectorProviderError
from a13n_service.connectivity.connectors.http import ConnectorHttpClient
from a13n_service.connectivity.connectors.providers.configuration import ApiKeyCredentials
from a13n_service.connectivity.connectors.providers.openconnector.configuration import OpenConnectorConfiguration
from a13n_service.connectivity.connectors.providers.openconnector.runtime import OpenConnectorRuntime
from pydantic import ValidationError


class AllowEndpoint:
    async def validate(self, value: str, *, resolve_dns: bool = False) -> str:
        return value


@pytest.fixture
def upstream():
    action = {
        "id": "github.get_current_user",
        "service": "github",
        "description": "Read current user",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "outputSchema": {"allOf": [{"type": "object"}]},
    }
    account = {
        "id": "account-1",
        "service": "github",
        "alias": "work",
        "isDefault": False,
        "status": "active",
        "credentialSummary": {"access_token": "never-expose"},
        "userId": "upstream-personal-user",
    }
    state = {"action": action, "account": account, "status": 200, "malformed": False}
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer dummy-key"
        assert "x-api-key" not in request.headers
        assert request.url.host == "connector.oomol.com"
        if request.method == "POST":
            assert request.url.path == "/v1/actions/github.get_current_user"
            assert request.headers["x-oo-connector-alias"] == "work"
            assert request.headers["idempotency-key"] == "smoke-call-1"
            assert json.loads(request.content) == {"input": {}}
            if state["malformed"]:
                return httpx2.Response(200, json=[])
            return httpx2.Response(state["status"], json={"success": True, "data": {"login": "demo"}})
        if request.url.path == "/v1/providers":
            data = [
                {"service": "17track", "displayName": "17TRACK", "authTypes": ["api_key"]},
                {"service": "github", "displayName": "GitHub", "authTypes": ["oauth2"]},
            ]
        elif request.url.path == "/v1/actions":
            assert dict(request.url.params) == {"service": "github"}
            data = [state["action"]]
        elif request.url.path == "/v1/actions/github.get_current_user":
            data = state["action"]
        elif request.url.path == "/v1/apps":
            data = [state["account"]]
        else:
            raise AssertionError(request.url)
        return httpx2.Response(200, json={"success": True, "message": "OK", "data": data})

    return state, requests, handler


def runtime(client):
    return OpenConnectorRuntime(
        ConnectorHttpClient(client, AllowEndpoint(), response_max_bytes=4 * 1024 * 1024),
        OpenConnectorConfiguration(),
        ApiKeyCredentials(api_key="dummy-key"),
    )


@pytest.mark.anyio
async def test_native_catalog_account_projection_and_explicit_action_call(upstream):
    _, requests, handler = upstream
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        native = runtime(client)
        providers = await native.providers()
        actions = await native.actions("github")
        assert providers[0].service == "17track"
        assert len(actions) == 1
        assert all(request.url.path != "/v1/apps" for request in requests)
        connections = await native.connections("github")
        assert "never-expose" not in repr(connections)
        assert "upstream-personal-user" not in repr(connections)
        result = await native.execute_action(
            action=actions[0], connection=connections[0], arguments={}, request_id="smoke-call-1"
        )
        assert result.kind == "succeeded"
        assert result.result == {"login": "demo"}
        assert not client.is_closed
    assert len([request for request in requests if request.method == "POST"]) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["id", "alias", "status", "schema", "provider", "ambiguous"])
async def test_changes_after_preview_prevent_dispatch(upstream, change):
    state, requests, handler = upstream
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        native = runtime(client)
        action = (await native.actions("github"))[0]
        account = (await native.connections("github"))[0]
        if change in {"id", "alias", "status"}:
            state["account"][change] = "changed"
        elif change == "schema":
            state["action"]["requiredScopes"] = ["new-scope"]
        elif change == "provider":
            state["action"]["service"] = "slack"
        else:
            state["account"]["alias"] = None
            account = (await native.connections("github"))[0]
        with pytest.raises(ConnectorProviderError):
            await native.execute_action(action=action, connection=account, arguments={}, request_id="smoke-call-1")
    assert all(request.method == "GET" for request in requests)


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["server", "malformed", "conflict"])
async def test_unknown_write_is_not_retried(upstream, failure):
    state, requests, handler = upstream
    state["status"] = {"server": 503, "conflict": 409, "malformed": 200}[failure]
    state["malformed"] = failure == "malformed"
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        native = runtime(client)
        action = (await native.actions("github"))[0]
        account = (await native.connections("github"))[0]
        outcome = await native.execute_action(
            action=action, connection=account, arguments={}, request_id="smoke-call-1"
        )
    assert outcome.kind == "outcome_unknown"
    assert len([request for request in requests if request.method == "POST"]) == 1


def test_configuration_rejects_unsupported_profiles_and_mismatched_deployments():
    with pytest.raises(ValidationError):
        OpenConnectorConfiguration(api_profile="unsupported")
    with pytest.raises(ValidationError):
        OpenConnectorConfiguration(endpoint="https://wrong.example")
    assert (
        OpenConnectorConfiguration(deployment="self_hosted", endpoint="https://runtime.example").api_profile
        == "runtime_v1"
    )


@pytest.mark.anyio
async def test_catalog_bounds_are_enforced(upstream, monkeypatch):
    from a13n_service.connectivity.connectors.providers import discovery

    monkeypatch.setattr(discovery, "DISCOVERY_MAX_TOOLS", 1)
    _, _, handler = upstream
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(ConnectorProviderError, match="directory_too_large"):
            await runtime(client).providers()
