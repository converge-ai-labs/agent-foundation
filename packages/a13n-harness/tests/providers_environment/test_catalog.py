"""Environment selection shares the immutable five-domain manifest contract."""

from dataclasses import FrozenInstanceError, replace

import pytest
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.builtins import BUILT_IN_ENVIRONMENT_PROVIDERS
from a13n_harness.providers.plugins import ProviderManifest


def test_all_native_definitions_have_immutable_typed_schemas():
    catalog = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)
    assert set(catalog) == {
        "direct_local",
        "local_envd",
        "docker",
        "e2b",
        "daytona",
        "modal",
        "vercel",
        "sprites",
        "runloop",
        "http_envd",
        "websocket_envd",
    }
    for definition in catalog.values():
        assert definition.configuration_model.model_json_schema()["type"] == "object"
        assert definition.environment_model.model_json_schema()["type"] == "object"
        with pytest.raises(FrozenInstanceError):
            definition.display_name = "changed"
    with pytest.raises(ValueError, match="duplicate"):
        ProviderCatalog((*catalog.values(), catalog["e2b"]))
    custom = replace(catalog["direct_local"], type="custom_workspace")
    assert ProviderManifest(api_version=1, environment=(custom,)).environment == (custom,)


def test_capability_declaration_is_the_single_source_and_must_be_consistent():
    connect_only = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)["http_envd"]
    assert not connect_only.supports_managed
    assert not (connect_only.supports_stop or connect_only.supports_destroy or connect_only.requires_keepalive)
    with pytest.raises(ValueError, match="Connect-only"):
        replace(connect_only, supports_stop=True)
