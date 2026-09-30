"""Operators can disable address restrictions without weakening URL or redirect rules."""

import ipaddress

import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://proxy-only.test/path",
        "https://127.0.0.1/path",
        "https://169.254.169.254/metadata",
        "https://[fd00:ec2::254]/",
    ],
)
async def test_disabled_ssrf_policy_skips_dns_and_address_checks(
    monkeypatch: pytest.MonkeyPatch, endpoint: str
) -> None:
    def unexpected_dns(*args, **kwargs):
        pytest.fail("Disabled SSRF policy must not resolve a destination")

    monkeypatch.setattr("a13n_harness.providers.endpoint_policy._resolve_addresses", unexpected_dns)
    policy = EndpointPolicy.from_operator_allowlist(require_https=True, ssrf_protection=False)
    assert await policy.validate(endpoint) == endpoint
    policy.validate_address("proxy-only.test", ipaddress.ip_address("169.254.169.254"))


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.com/",
        "file:///etc/passwd",
        "https://user:password@example.com/",
        "https://example.com/?token=secret",
        "https://example.com/#fragment",
    ],
)
async def test_disabled_ssrf_policy_keeps_url_rules(endpoint: str) -> None:
    with pytest.raises(EndpointPolicyError):
        await EndpointPolicy(ssrf_protection=False, require_https=True).validate(endpoint)


async def test_disabled_ssrf_policy_keeps_redirect_credential_boundary() -> None:
    policy = EndpointPolicy(ssrf_protection=False)
    assert await policy.validate_redirect("https://source.test/", "https://other.test/path") == (
        "https://other.test/path",
        False,
    )
    with pytest.raises(EndpointPolicyError, match="cannot redirect"):
        await policy.validate_redirect("https://source.test/", "http://source.test/")


@pytest.mark.parametrize("endpoint", ["https://127.0.0.1/", "https://169.254.169.254/", "https://[fd00:ec2::254]/"])
async def test_ssrf_policy_remains_enabled_by_default(endpoint: str) -> None:
    with pytest.raises(EndpointPolicyError):
        await EndpointPolicy().validate(endpoint)
