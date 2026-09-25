"""The shared `ProviderDefinition` core validates identically in all four domains."""

from dataclasses import replace

import pytest
from a13n_harness.providers.authentication import Authentication, AuthenticationCase, CredentialMode
from a13n_harness.providers.connector.builtins import COMPOSIO
from a13n_harness.providers.environment.builtins import BUILT_IN_ENVIRONMENT_PROVIDERS
from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS
from a13n_harness.providers.web.builtins import built_in_web_providers

CREDENTIALED = [
    pytest.param(built_in_web_providers()[1], "Web", {}, id="web"),
    pytest.param(BUILT_IN_MODEL_PROVIDERS[0], "Model", {}, id="model"),
    pytest.param(COMPOSIO, "Connector", {}, id="connector"),
    pytest.param(
        next(item for item in BUILT_IN_ENVIRONMENT_PROVIDERS if item.type == "e2b"), "Environment", {}, id="environment"
    ),
]
NO_CREDENTIAL = [
    pytest.param(built_in_web_providers()[0], id="web"),
    pytest.param(
        next(item for item in BUILT_IN_ENVIRONMENT_PROVIDERS if item.type == "direct_local"), id="environment"
    ),
]


@pytest.mark.parametrize(("definition", "domain", "configuration"), CREDENTIALED)
def test_every_domain_rejects_the_same_invalid_metadata(definition, domain, configuration):
    with pytest.raises(ValueError, match=f"{domain} Provider .* is invalid"):
        replace(definition, type="Not A Type")
    with pytest.raises(ValueError, match="invalid display name"):
        replace(definition, display_name="   ")
    with pytest.raises(ValueError, match="invalid setup URL"):
        replace(definition, setup_url="http://example.com")
    with pytest.raises(ValueError, match="invalid setup label"):
        replace(definition, setup_url=None, setup_label="Configure access")
    with pytest.raises(ValueError, match="invalid schema"):
        replace(definition, configuration_model=int)
    with pytest.raises(ValueError, match="must name configuration fields"):
        replace(
            definition,
            authentication=Authentication(
                cases=(AuthenticationCase(field="absent_field", equals=True, mode=CredentialMode.forbidden),)
            ),
        )


@pytest.mark.parametrize("definition", NO_CREDENTIAL)
def test_no_credential_model_forbids_credentials_without_declaring_authentication(definition):
    assert definition.credential_model is None
    assert definition.authentication == Authentication(mode=CredentialMode.forbidden)
    assert definition.parse_credential(definition.configuration_model(), None) is None
    with pytest.raises(ValueError, match="does not accept a credential"):
        definition.parse_credential(definition.configuration_model(), {"api_key": "secret"})
    with pytest.raises(ValueError, match="cannot accept one"):
        replace(definition, authentication=Authentication(mode=CredentialMode.optional))


@pytest.mark.parametrize(("definition", "domain", "configuration"), CREDENTIALED)
def test_required_credentials_are_enforced_before_a_provider_is_opened(definition, domain, configuration):
    if definition.authentication.mode is not CredentialMode.required:
        pytest.skip(f"{domain} built-in does not require a credential")
    parsed = definition.configuration_model.model_validate(configuration)
    with pytest.raises(ValueError, match="credential is required"):
        definition.parse_credential(parsed, None)
    assert definition.parse_credential(parsed, {"api_key": "secret"}) is not None
