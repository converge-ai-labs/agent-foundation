"""Environment endpoint prechecks honor the operator's SSRF switch without dropping URL boundaries."""

import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.environment.errors import EnvironmentProviderError, EnvironmentProviderErrorCategory
from a13n_service.providers.endpoints import check_endpoint

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("endpoint", ["https://proxy-only.test", "http://10.1.2.3:2375", "http://169.254.169.254"])
async def test_disabled_protection_skips_environment_dns_prechecks(
    endpoint: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unexpected_dns(*args, **kwargs):
        pytest.fail("disabled SSRF protection must not pre-resolve environment endpoints")

    monkeypatch.setattr("a13n_service.infra.outbound.anyio.getaddrinfo", unexpected_dns)
    await check_endpoint("envd", endpoint, EndpointPolicy(ssrf_protection=False))


async def test_enabled_protection_keeps_environment_dns_failure_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unavailable_dns(*args, **kwargs):
        raise OSError("no local DNS")

    monkeypatch.setattr("a13n_service.infra.outbound.anyio.getaddrinfo", unavailable_dns)
    with pytest.raises(EnvironmentProviderError) as failure:
        await check_endpoint("envd", "https://proxy-only.test", EndpointPolicy())
    assert failure.value.category is EnvironmentProviderErrorCategory.UNAVAILABLE
    assert failure.value.code == "provider_unavailable"


@pytest.mark.parametrize("ssrf_protection", [False, True])
async def test_environment_prechecks_keep_syntax_boundaries(ssrf_protection: bool) -> None:
    with pytest.raises(EnvironmentProviderError) as failure:
        await check_endpoint(
            "envd", "https://user:password@example.com", EndpointPolicy(ssrf_protection=ssrf_protection)
        )
    assert failure.value.category is EnvironmentProviderErrorCategory.DENIED
    assert failure.value.code == "provider_endpoint_denied"


async def test_enabled_protection_keeps_environment_address_restrictions(monkeypatch: pytest.MonkeyPatch) -> None:
    async def private_dns(*args, **kwargs):
        return [(None, None, None, None, ("10.1.2.3", 443))]

    monkeypatch.setattr("a13n_service.infra.outbound.anyio.getaddrinfo", private_dns)
    with pytest.raises(EnvironmentProviderError) as failure:
        await check_endpoint("envd", "https://private.test", EndpointPolicy())
    assert failure.value.category is EnvironmentProviderErrorCategory.DENIED
    assert failure.value.code == "provider_endpoint_denied"
