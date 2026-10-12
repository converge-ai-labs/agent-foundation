"""E2B-compatible service selection reaches the native runtime with a well-formed sandbox domain."""

import pytest
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.e2b.configuration import E2BConnectionConfiguration, E2BCredential
from a13n_harness.providers.environment.e2b.execution import E2BExecution
from a13n_harness.providers.environment.e2b.provider import E2B as HARNESS_E2B
from a13n_service.providers.environments.e2b import E2B, Account
from a13n_service.providers.environments.e2b.management import E2BManagement
from pydantic import ValidationError


@pytest.mark.parametrize("domain", ["e2b.dev", "sandboxes.example.com", "region-1.sandboxes.example.com"])
@pytest.mark.anyio
async def test_management_and_execution_use_the_selected_service_domain(domain: str, monkeypatch) -> None:
    observed = []

    async def inspect(backend):
        observed.append(dict(backend._options()))
        return "running"

    async def open(backend, *, execution_id):
        observed.append(dict(backend._options()))

    async def ready(backend, operations):
        pass

    monkeypatch.setattr(E2BManagement, "inspect", inspect)
    monkeypatch.setattr(E2BExecution, "open", open)
    monkeypatch.setattr(E2BExecution, "check_ready", ready)
    account = Account(domain=domain)
    credential = E2BCredential(api_key="test-secret")
    recipe = E2B.validate_environment({})
    state = EnvironmentState(
        provider_key="e2b",
        state_version="1",
        state={
            "sandbox_id": "sandbox-existing",
            "environment_id": "env-existing",
            "configuration_fingerprint": recipe.fingerprint,
        },
    )
    async with await E2B.open_provider(configuration=account, credential=credential) as provider:
        assert (await provider.inspect(recipe, environment_id="env-existing", state=state)).status == "running"
    connector = E2B.execution.execution_connector(
        E2B.execution_environment(recipe),
        configuration=account,
        credential=credential,
        environment_id="env-existing",
        state=state,
    )
    async with await connector.open():
        pass
    assert len(observed) == 2
    for options in observed:
        assert options["domain"] == domain
        assert options["api_url"] == f"https://api.{domain}"
        assert options["api_key"] == credential.api_key.get_secret_value()
    assert E2B.backend_identity(account) == HARNESS_E2B.backend_identity(account.connection())


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
