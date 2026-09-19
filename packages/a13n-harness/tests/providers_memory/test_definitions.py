import pytest
from a13n_harness.providers.memory.configuration import Mem0Credential, Mem0OSSConfiguration
from pydantic import ValidationError


@pytest.mark.parametrize(
    "url", ["file:///tmp/memory", "https://user:secret@host", "https://host?key=secret", "https://host#fragment"]
)
def test_mem0_configuration_keeps_credentials_out_of_urls(url):
    with pytest.raises(ValidationError):
        Mem0OSSConfiguration(base_url=url)


def test_mem0_configuration_and_credential_are_independent_pure_models():
    config = Mem0OSSConfiguration(base_url="http://localhost:8000/")
    assert config.base_url == "http://localhost:8000"
    with pytest.raises(ValidationError):
        Mem0OSSConfiguration(base_url="http://localhost", api_key="secret")
    credential = Mem0Credential(api_key="secret")
    assert "secret" not in repr(credential)
    assert "secret" not in credential.model_dump_json()
    with pytest.raises(ValidationError):
        Mem0Credential(api_key="   ")
    with pytest.raises(ValidationError):
        config.base_url = "http://other"
