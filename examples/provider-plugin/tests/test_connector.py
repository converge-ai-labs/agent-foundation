import json

import httpx2
import pytest
from a13n_harness.providers.connector.contracts import ConnectionBinding, SetupContext
from a13n_harness.providers.connector.http import ConnectorHttpClient
from a13n_harness.providers.plugins import load_provider_plugins


class Endpoint:
    async def validate(self, endpoint, *, resolve_dns=True):
        return endpoint


@pytest.mark.anyio
async def test_installed_connector_direct_protocol_and_structured_credentials():
    requests = []
    external_user = "embedded-user"

    def remote(request):
        requests.append(request)
        assert request.headers["x-api-key"] == "nested-secret"
        assert request.headers["x-revision"] == "7"
        assert request.headers["x-tier"] == "sandbox"
        if request.url.path == "/connections":
            assert json.loads(request.content)["user"] == external_user
            value = {"id": "external-account"}
        elif request.url.path == "/connections/external-account" and request.method == "GET":
            value = dict(
                external_ref="external-account",
                connector_key="crm",
                external_user_correlation=external_user,
                status="ready",
                safe_metadata={},
                provider_version="v1",
            )
        else:
            value = {"ok": True}
        return httpx2.Response(200, json=value)

    definition = load_provider_plugins(("acme",))[0].manifest.connector[0]
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(remote)) as client:
        async with definition.open(
            {"region": "eu"},
            {"authorization": {"token": "nested-secret"}, "revision": 7, "tier": "sandbox"},
            http=ConnectorHttpClient(client, Endpoint(), response_max_bytes=4096),
        ) as provider:
            assert await provider.test() == ("account_read",)
            assert (await provider.discover_connectors())[0].key == "crm"
            context = SetupContext(connector_key="crm", external_user_correlation=external_user)
            setup = await provider.start_setup(setup={}, context=context)
            inspection = await provider.inspect_setup(setup_ref=setup.setup_ref, context=context)
            assert inspection is not None
            connected = provider.connect(
                ConnectionBinding(
                    external_ref=inspection.external_ref, connector_key="crm", external_user_correlation=external_user
                )
            )
            tools = await connected.discover_tools(cursor=None)
            outcome = await connected.execute_tool(
                tool_key=tools.items[0].key, provider_version=tools.provider_version, arguments={}, request_id="call-1"
            )
            assert outcome.result == {"ok": True}
            await connected.revoke(operation_id="revoke-1")
        assert not client.is_closed
    assert len(requests) == 6
