"""Exact Run host authorization preserves independent URL and redirect boundaries."""

import socket

import pytest
from a13n_harness import RunConfiguration
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
async def test_unrestricted_policy_never_pre_resolves(endpoint, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("URL authorization must not resolve DNS")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected)
    assert await EndpointPolicy(require_https=True).validate(endpoint) == endpoint


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
async def test_url_rules_are_independent_of_host_authorization(endpoint):
    with pytest.raises(EndpointPolicyError):
        await EndpointPolicy(require_https=True).validate(endpoint)


async def test_redirects_preserve_hostname_and_credential_boundaries():
    policy = EndpointPolicy(configuration=RunConfiguration(allowed_hosts={"source.test", "other.test"}))
    assert await policy.validate_redirect("https://source.test/", "https://other.test/path") == (
        "https://other.test/path",
        False,
    )
    with pytest.raises(EndpointPolicyError, match="cannot redirect"):
        await policy.validate_redirect("https://source.test/", "http://source.test/")
    with pytest.raises(EndpointPolicyError, match="not allowed"):
        await policy.validate_redirect("https://source.test/", "https://denied.test/")


async def test_http_exceptions_are_exact_origins_and_do_not_widen_allowed_hosts():
    policy = EndpointPolicy.from_http_origins(require_https=True, http_origins=["http://local.test:8080"])
    policy = policy.for_run(RunConfiguration(allowed_hosts={"local.test"}))
    assert await policy.validate("http://local.test:8080/path") == "http://local.test:8080/path"
    for url in ("http://local.test", "http://other.test:8080"):
        with pytest.raises(EndpointPolicyError):
            await policy.validate(url)
