"""Service composes execution metadata without changing persisted target meaning."""

from dataclasses import replace

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.configuration import EnvironmentProviderConfiguration
from a13n_harness.providers.environment.docker.shared import _fingerprint
from a13n_harness.providers.environment.e2b.configuration import E2BCredential
from a13n_service.providers.environments.docker.definition import DOCKER
from a13n_service.providers.environments.e2b.definition import E2B
from pydantic import BaseModel


@pytest.mark.parametrize(
    "changes",
    [
        {"type": "other"},
        {"configuration_model": EnvironmentProviderConfiguration},
        {"credential_model": E2BCredential},
    ],
)
def test_management_rejects_different_execution_identity(changes):
    with pytest.raises(ValueError, match="must share"):
        replace(DOCKER, **changes)


def test_management_requires_shared_execution_input_model_and_factory():
    with pytest.raises(TypeError, match="extend"):
        replace(DOCKER, environment_model=BaseModel)
    with pytest.raises(TypeError, match="factory"):
        replace(DOCKER, provider_factory=None)
    with pytest.raises(TypeError, match="booleans"):
        replace(DOCKER, supports_destroy="true")


# Fingerprints captured from revision 76bfea20; persisted recipes/state must survive the ownership change.
@pytest.mark.parametrize(
    "inputs, fingerprint",
    [
        ({}, "12409a53f006e75584be211cf262d53b0d15068603c9f6a95a3c11418d1df69c"),
        (
            {"image": "example:v1", "environment": {"LANG": "C"}, "cpus": 2, "pull_policy": "never"},
            "2c32b83b57f2abcd5e7979900d74fcf6f3b7ae607f3306dcccae80edfea14d2c",
        ),
    ],
)
def test_docker_projects_management_recipe_without_changing_fingerprint(inputs, fingerprint):
    recipe = DOCKER.validate_environment(inputs)
    execution = DOCKER.execution_environment(recipe.model_dump(mode="json"))
    assert "pull_policy" not in type(execution).model_fields
    assert _fingerprint(recipe) == _fingerprint(execution) == fingerprint
    state = EnvironmentState(
        provider_key="docker",
        state_version="1",
        state={
            "environment_id": "env-existing",
            "container_id": "container-existing",
            "configuration_fingerprint": fingerprint,
        },
    )
    assert DOCKER.target_identity(configuration=recipe, state=state) == "container-existing"
    assert DOCKER.execution.execution_connector(execution, state=state).state == state
    with pytest.raises(EnvironmentProviderError):
        DOCKER.execution.validate_environment(recipe.model_dump(mode="json"))


@pytest.mark.parametrize(
    "inputs, fingerprint",
    [
        ({}, "eb7ed50983c2f823a21bfd3049a3cd5b60387717edaaf9537a7f1f04abd3cb54"),
        (
            {
                "template": "example",
                "root": "/work",
                "user": "runner",
                "allow_internet_access": False,
                "timeout_seconds": 120,
            },
            "53c48d6849d958825dfa47b9228c2af12701ab513002e4a365b26c817747dfba",
        ),
    ],
)
def test_e2b_projects_management_recipe_without_changing_fingerprint(inputs, fingerprint):
    recipe = E2B.validate_environment(inputs)
    execution = E2B.execution_environment(recipe.model_dump(mode="json"))
    assert "timeout_seconds" not in type(execution).model_fields
    assert recipe.fingerprint == execution.fingerprint == fingerprint
    state = EnvironmentState(
        provider_key="e2b",
        state_version="1",
        state={
            "environment_id": "env-existing",
            "sandbox_id": "sandbox-existing",
            "configuration_fingerprint": fingerprint,
        },
    )
    assert E2B.target_identity(configuration=recipe, state=state) == "sandbox-existing"
    assert E2B.execution.execution_connector(execution, credential={"api_key": "secret"}, state=state).state == state
    with pytest.raises(EnvironmentProviderError):
        E2B.execution.validate_environment(recipe.model_dump(mode="json"))
