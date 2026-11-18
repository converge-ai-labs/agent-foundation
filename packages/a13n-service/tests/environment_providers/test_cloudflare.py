import pytest
from a13n_service.providers.endpoints import dialed_endpoint
from a13n_service.providers.environment.cloudflare import Account
from a13n_service.providers.environments.cloudflare import CLOUDFLARE
from a13n_service.providers.environments.cloudflare.execution import Recipe
from pydantic import ValidationError


def test_cloudflare_defaults_to_http_and_public_network():
    recipe = CLOUDFLARE.validate_environment({})
    assert recipe.model_dump(exclude={"environment", "envd"}) == {"execution": "envd_http", "network_egress": "public"}


@pytest.mark.parametrize("execution", ["envd_http", "envd_ws"])
def test_cloudflare_disabling_network_does_not_select_the_transport(execution):
    recipe = CLOUDFLARE.validate_environment({"execution": execution, "network_egress": "disabled"})
    assert recipe.model_dump(exclude={"environment", "envd"}) == {"execution": execution, "network_egress": "disabled"}


def test_cloudflare_rejects_unsupported_network_policy():
    with pytest.raises(ValidationError, match=r"public.*disabled"):
        Recipe.model_validate({"network_egress": "allowlist"})


def test_cloudflare_gateway_origin_is_discoverable_for_endpoint_policy():
    assert dialed_endpoint(Account(endpoint="https://gateway.example/")) == "https://gateway.example"
    with pytest.raises(ValidationError, match="HTTPS gateway origin"):
        Account(endpoint="http://gateway.example")
