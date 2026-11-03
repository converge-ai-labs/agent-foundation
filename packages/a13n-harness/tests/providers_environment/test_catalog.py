"""The built-in Environment catalog and its capability declarations."""

from dataclasses import replace

import pytest
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.builtins import BUILT_IN_ENVIRONMENT_PROVIDERS


def test_capability_declaration_is_the_single_source_and_must_be_consistent():
    connect_only = ProviderCatalog(BUILT_IN_ENVIRONMENT_PROVIDERS)["http_envd"]
    with pytest.raises(TypeError, match="supports_stop"):
        replace(connect_only, supports_stop=True)
