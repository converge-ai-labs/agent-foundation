"""E2B-compatible service selection reaches the native runtime with a well-formed sandbox domain."""

import pytest
from a13n_harness.providers.environment.e2b.configuration import E2BConnectionConfiguration, E2BCredential
from a13n_harness.providers.environment.e2b.provider import E2B as HARNESS_E2B
from a13n_harness.providers.environment.e2b.provider import E2BProviderRuntime
from a13n_service.providers.environments.e2b import E2B, Account
from pydantic import ValidationError


@pytest.mark.parametrize("domain", ["e2b.dev", "sandboxes.example.com", "region-1.sandboxes.example.com"])
@pytest.mark.anyio
async def test_runtime_uses_the_selected_service_domain(domain: str) -> None:
    assert E2B.runtime_factory is not None
    account = Account(domain=domain)
    credential = E2BCredential(api_key="test-secret")
    runtime = await E2B.runtime_factory(configuration=account, credential=credential)
    assert isinstance(runtime, E2BProviderRuntime)
    assert runtime.domain == domain and runtime.api_url is None
    assert runtime.api_key == credential.api_key
    assert E2B.backend_identity(account) == HARNESS_E2B.backend_identity(E2BConnectionConfiguration(domain=domain))


def test_default_identity_is_stable_and_services_are_distinct() -> None:
    assert E2B.backend_identity(Account()) == HARNESS_E2B.backend_identity(E2BConnectionConfiguration())
    identities = [
        E2B.backend_identity(Account(domain=d))
        for d in ["e2b.dev", "sandboxes.example.com", "region-2.sandboxes.example.com"]
    ]
    assert all(a != b for index, a in enumerate(identities) for b in identities[index + 1 :])


@pytest.mark.parametrize(
    "config",
    [
        {"domain": "localhost"},
        {"domain": "a..example.com"},
        {"domain": "-a.example.com"},
        {"domain": "a-.example.com"},
        {"domain": "a_b.example.com"},
        {"domain": "a" * 64 + ".example.com"},
        {"domain": ("a" * 63 + ".") * 4 + "com"},
        {"domain": "127.0.0.1"},
        {"domain": "sandboxes.example.com:443"},
        {"domain": "https://sandboxes.example.com"},
        {"domain": "sandboxes.example.com\n"},
        {"api_url": "https://api.sandboxes.example.com"},
        {"api_url": "http://169.254.169.254"},
    ],
)
def test_accounts_reject_malformed_domains_and_api_overrides(config: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        Account.model_validate(config)
