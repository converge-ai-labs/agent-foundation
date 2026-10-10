"""Environment connections authorize declared Run hosts without local DNS prechecks."""

import socket

import pytest
from a13n_environment.errors import EnvironmentProviderError, EnvironmentProviderErrorCategory
from a13n_harness import RunConfiguration
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.providers.endpoints import check_endpoint

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("endpoint", ["https://proxy-only.test", "http://10.1.2.3:2375", "http://169.254.169.254"])
async def test_environment_prechecks_do_not_resolve_dns(endpoint, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Environment URL authorization must not resolve DNS")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected)
    await check_endpoint("envd", endpoint, EndpointPolicy())


@pytest.mark.parametrize("endpoint", ["https://user:password@example.com", "https://denied.test"])
async def test_environment_prechecks_keep_url_and_run_hostname_boundaries(endpoint):
    with pytest.raises(EnvironmentProviderError) as failure:
        await check_endpoint(
            "envd", endpoint, EndpointPolicy(configuration=RunConfiguration(allowed_hosts={"example.com"}))
        )
    assert failure.value.category is EnvironmentProviderErrorCategory.DENIED
    assert failure.value.code == "provider_endpoint_denied"
