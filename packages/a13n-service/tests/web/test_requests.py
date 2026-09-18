import pytest
from a13n_service.web.domain import CreateWebProviderRequest, UpdateWebProviderRequest
from a13n_service.web.registry import built_in_web_provider_registry
from pydantic import ValidationError


def test_account_request_and_configuration_validation():
    request = CreateWebProviderRequest(type="brave", name="  Work  ", credential={"api_key": " key "})
    assert request.name == "Work"
    assert request.credential == {"api_key": " key "}
    with pytest.raises(ValidationError):
        UpdateWebProviderRequest(credential=None)
    with pytest.raises(ValidationError):
        built_in_web_provider_registry().validate_configuration("exa", {"endpoint": "https://example.com"})
