"""The built-in Environment catalog and its capability declarations."""

from dataclasses import replace

import pytest
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.builtins import BUILT_IN_ENVIRONMENT_PROVIDERS


def test_all_native_definitions_have_typed_schemas():
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
    assert len(catalog) == len(BUILT_IN_ENVIRONMENT_PROVIDERS)
    for definition in catalog.values():
        assert definition.configuration_model.model_json_schema()["type"] == "object"
        assert definition.environment_model.model_json_schema()["type"] == "object"


def test_capability_declaration_is_the_single_source_and_must_be_consistent():
    connect_only = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)["http_envd"]
    with pytest.raises(ValueError, match="Connect-only"):
        replace(connect_only, supports_stop=True)
