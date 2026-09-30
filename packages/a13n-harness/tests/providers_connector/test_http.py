"""Public transport validates the destination before credential dispatch."""

import anyio
import httpx2
import pytest
from a13n_harness.providers.connector.contracts import ConnectorProviderError
from a13n_harness.providers.connector.http import ConnectorHttpClient

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("path", ["@127.0.0.1/private", "//other.example/private", "/\\other", "/path#fragment"])
async def test_malformed_path_never_dispatches(path):
    class Policy:
        async def validate(self, endpoint):
            pytest.fail("Malformed path reached policy")

    def send(request):
        pytest.fail("Malformed path sent credentials")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(send)) as client:
        transport = ConnectorHttpClient(client, Policy(), response_max_bytes=128)
        with pytest.raises(ConnectorProviderError) as error:
            await transport.request(
                "POST", endpoint="https://approved.example", path=path, api_key="secret", write=True
            )
        assert error.value.code == "endpoint_denied"
        assert not error.value.outcome_unknown


async def test_final_url_query_validated_and_redirect_not_followed():
    validated = []
    sent = []

    class Policy:
        async def validate(self, endpoint):
            validated.append(endpoint)
            return endpoint

    def send(request):
        sent.append(request)
        return httpx2.Response(302, headers={"location": "https://other.example/stolen"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(send), follow_redirects=True) as client:
        transport = ConnectorHttpClient(client, Policy(), response_max_bytes=128)
        with pytest.raises(ConnectorProviderError) as error:
            await transport.request(
                "GET",
                endpoint="https://approved.example/base",
                path="/path?first=1",
                params={"second": "two"},
                api_key="secret",
            )
    assert error.value.code == "provider_rejected"
    assert len(sent) == 1
    assert validated == [str(sent[0].url)] == ["https://approved.example/base/path?first=1&second=two"]
    assert sent[0].headers["x-api-key"] == "secret"


async def test_policy_resolution_is_inside_budget_without_unknown_write():
    class Policy:
        async def validate(self, endpoint):
            await anyio.sleep_forever()

    def send(request):
        pytest.fail("Preflight timeout dispatched")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(send)) as client:
        transport = ConnectorHttpClient(client, Policy(), response_max_bytes=128, timeout_seconds=0.01)
        with anyio.fail_after(0.2), pytest.raises(ConnectorProviderError) as error:
            await transport.request(
                "POST", endpoint="https://approved.example", path="/write", api_key="secret", write=True
            )
    assert error.value.code == "provider_unavailable"
    assert not error.value.outcome_unknown


async def test_connector_hostname_denial_happens_before_dispatch():
    from a13n_harness import RunConfiguration
    from a13n_harness.providers.endpoint_policy import EndpointPolicy

    def send(request):
        pytest.fail("Denied hostname must not be dispatched")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(send)) as client:
        policy = EndpointPolicy(configuration=RunConfiguration(allowed_hosts=set()))
        transport = ConnectorHttpClient(client, policy, response_max_bytes=128, timeout_seconds=0.1)
        with pytest.raises(ConnectorProviderError) as error:
            await transport.request("POST", endpoint="https://denied.test", path="/write", write=True)
        assert not error.value.outcome_unknown
