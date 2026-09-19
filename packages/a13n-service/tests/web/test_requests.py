import pytest
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.web.builtins import built_in_web_providers
from a13n_service.web.domain import CreateWebProviderRequest, UpdateWebProviderRequest
from pydantic import ValidationError


def test_account_request_and_configuration_validation():
    request = CreateWebProviderRequest(type="brave", name="  Work  ", credential={"api_key": " key "})
    assert request.name == "Work"
    assert request.credential == {"api_key": " key "}
    assert UpdateWebProviderRequest(credential=None).model_fields_set == {"credential"}
    with pytest.raises(ValidationError):
        ProviderCatalog(built_in_web_providers()).require("exa").configuration_model.model_validate(
            {"endpoint": "https://example.com"}
        )
