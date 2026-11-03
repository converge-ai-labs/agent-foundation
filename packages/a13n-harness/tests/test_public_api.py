from __future__ import annotations

import a13n_harness as harness
import a13n_harness.capabilities as capabilities
import a13n_harness.environment as environment
import a13n_harness.environment.advanced as advanced_environment
import a13n_harness.filters as filters
import a13n_harness.models as models
import a13n_harness.pricing as pricing
import a13n_harness.providers as providers
import a13n_harness.providers.environment as environment_provider
import a13n_harness.providers.memory as memory_provider
import a13n_harness.providers.model.oauth as model_auth
import a13n_harness.tools as tools
import a13n_harness.toolsets as toolsets
import pytest


@pytest.mark.parametrize(
    "module",
    [
        harness,
        capabilities,
        environment,
        advanced_environment,
        filters,
        models,
        pricing,
        providers,
        environment_provider,
        memory_provider,
        model_auth,
        tools,
        toolsets,
    ],
)
def test_declared_public_exports_resolve(module) -> None:
    for name in module.__all__:
        getattr(module, name)
