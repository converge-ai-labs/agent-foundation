"""Cloud supplier HTTP behavior with no external requests."""

import httpx2
import pytest
from a13n_harness.configuration import RunConfiguration
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.providers.browser.cloud import CloudAPI, CloudCredential
from a13n_service.providers.browser.definition import BrowserProviderError
from pydantic import SecretStr

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("supplier,header", [("browserbase", "X-BB-API-Key"), ("kernel", "Authorization")])
async def test_cloud_request_uses_authorized_supplier_without_redirect(supplier, header):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx2.Response(302, headers={"Location": "https://example.org/"}, request=request)

    api = CloudAPI(
        supplier,
        CloudCredential(api_key=SecretStr("fixture-secret")),
        EndpointPolicy(),
        transport=httpx2.MockTransport(handler),
    )
    try:
        with pytest.raises(BrowserProviderError) as error:
            await api.request("POST", "/sessions", body={})
        assert error.value.certainty == "unknown"
        assert "fixture-secret" not in str(error.value)
        assert len(requests) == 1
        assert "fixture-secret" in requests[0].headers[header]
    finally:
        await api.close()


async def test_cloud_denied_endpoint_is_not_dispatched():
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx2.Response(200, request=request)

    policy = EndpointPolicy(configuration=RunConfiguration(allowed_hosts=["example.org"]))
    api = CloudAPI(
        "kernel", CloudCredential(api_key=SecretStr("fixture-secret")), policy, transport=httpx2.MockTransport(handler)
    )
    try:
        with pytest.raises(BrowserProviderError) as error:
            await api.request("POST", "/browsers", body={})
        assert error.value.certainty == "not_dispatched"
        assert calls == []
    finally:
        await api.close()


async def test_cloud_mutation_transport_failure_is_unknown_without_retry():
    calls = []

    async def handler(request):
        calls.append(request)
        raise httpx2.ReadTimeout("sensitive response", request=request)

    api = CloudAPI(
        "kernel",
        CloudCredential(api_key=SecretStr("fixture-secret")),
        EndpointPolicy(),
        transport=httpx2.MockTransport(handler),
    )
    try:
        with pytest.raises(BrowserProviderError) as error:
            await api.request("POST", "/browsers", body={})
        assert error.value.certainty == "unknown"
        assert "sensitive" not in str(error.value)
        assert len(calls) == 1
    finally:
        await api.close()


async def test_cloud_validates_effective_request_url_before_dispatch():
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx2.Response(200, request=request)

    api = CloudAPI(
        "kernel",
        CloudCredential(api_key=SecretStr("fixture-secret")),
        EndpointPolicy(),
        transport=httpx2.MockTransport(handler),
    )
    # The client's actual base URL, not just the configured supplier string, must
    # be checked before sending its default credential header.
    api.client.base_url = "https://api.onkernel.com:444/"
    try:
        with pytest.raises(BrowserProviderError) as error:
            await api.request("POST", "/browsers", body={})
        assert error.value.certainty == "not_dispatched"
        assert not calls
    finally:
        await api.close()


@pytest.mark.parametrize("encoding,content", [("identity", b"12345"), ("gzip", b"compressed")])
async def test_cloud_rejects_oversized_or_compressed_mutation_response(encoding, content):
    calls = []

    class Stream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            yield content

    async def handler(request):
        calls.append(request)
        return httpx2.Response(200, headers={"content-encoding": encoding}, stream=Stream(), request=request)

    api = CloudAPI(
        "kernel",
        CloudCredential(api_key=SecretStr("fixture")),
        EndpointPolicy(),
        transport=httpx2.MockTransport(handler),
        max_bytes=4,
    )
    try:
        with pytest.raises(BrowserProviderError) as error:
            await api.request("POST", "/browsers", body={})
        assert error.value.certainty == "unknown"
        assert len(calls) == 1
        assert calls[0].headers["accept-encoding"] == "identity"
    finally:
        await api.close()
