"""Shared template configuration composes with platform and exporter bootstrap."""

import json

import pytest
from a13n_harness.environment.envd_settings import EnvdSettings
from a13n_service.distribution import OSS
from a13n_service.providers.environment.envd import envd_environment
from a13n_service.providers.environment.recipes.docker import DockerRecipe
from a13n_service.providers.registry import Registry
from pydantic import ValidationError

RECIPES = [
    ("docker", {}),
    ("e2b", {}),
    ("daytona", {"snapshot": "envd-image"}),
    ("modal", {"image": "envd-image", "startup_command": ["a13n-envd"]}),
    ("sprites", {}),
    ("vercel", {}),
    ("runloop", {}),
    ("local", {"root": {"path": "/workspace"}}),
]


@pytest.mark.parametrize("provider,recipe", RECIPES)
def test_every_template_retains_typed_envd_overrides_and_empty_legacy_identity(provider, recipe):
    definition = Registry.of(OSS.providers).get("environment", provider)
    old = definition.validate_environment(recipe)
    empty = definition.validate_environment({**recipe, "envd": {"limits": {}}})
    assert "envd" not in old.model_dump(mode="json")
    assert old.model_dump_json() == empty.model_dump_json()
    supplied = {"limits": {"max_concurrent_file_transfers": 32}, "idle_timeout_ms": 12345}
    configured = definition.validate_environment({**recipe, "envd": supplied})
    frozen = json.loads(configured.model_dump_json())
    assert frozen["envd"] == supplied
    assert definition.validate_environment(frozen).model_dump_json() == configured.model_dump_json()
    execution = definition.execution_environment(frozen)
    assert execution.envd.overrides() == supplied
    if hasattr(old, "fingerprint"):
        assert old.fingerprint == empty.fingerprint
        assert old.fingerprint != configured.fingerprint


def test_empty_settings_leave_launch_payload_byte_for_byte_unchanged():
    values = {"A13N_ENVD_CONFIG_JSON": '{ "monitoring": {"sample_interval_ms":1000} }', "APP_MODE": "test"}
    assert envd_environment(values, EnvdSettings()) is values


def test_template_overrides_merge_limits_without_losing_exporter_or_platform_settings():
    values = {
        "APP_MODE": "test",
        "A13N_ENVD_DEVICE_ID": "env-test",
        "A13N_ENVD_FULL_CONTROL": "true",
        "A13N_ENVD_CONFIG_JSON": json.dumps(
            {
                "monitoring": {"sample_interval_ms": 1000},
                "limits": {"max_file_bytes": 123, "max_concurrent_file_transfers": 64},
            }
        ),
    }
    settings = EnvdSettings.model_validate({"limits": {"max_concurrent_file_transfers": 32}, "idle_timeout_ms": 3000})
    result = envd_environment(values, settings)
    assert json.loads(result["A13N_ENVD_CONFIG_JSON"]) == {
        "monitoring": {"sample_interval_ms": 1000},
        "limits": {"max_file_bytes": 123, "max_concurrent_file_transfers": 32},
        "idle_timeout_ms": 3000,
    }
    assert result["APP_MODE"] == "test"
    assert result["A13N_ENVD_DEVICE_ID"] == "env-test"
    assert result["A13N_ENVD_FULL_CONTROL"] == "true"
    assert settings.overrides() == {"limits": {"max_concurrent_file_transfers": 32}, "idle_timeout_ms": 3000}
    assert json.loads(values["A13N_ENVD_CONFIG_JSON"])["limits"]["max_concurrent_file_transfers"] == 64


def test_docker_validates_effective_legacy_limits_before_launch():
    with pytest.raises(ValidationError, match="both output streams"):
        DockerRecipe(execution="envd_http", envd={"limits": {"max_output_bytes_per_stream": 128 * 1024 * 1024}})
    config = DockerRecipe(
        execution="envd_http",
        envd={"limits": {"max_output_bytes_per_stream": 128 * 1024 * 1024, "max_spool_bytes": 256 * 1024 * 1024}},
    )
    assert config.launch_limits()["max_spool_bytes"] == 256 * 1024 * 1024


def test_envd_validates_merged_limits_instead_of_overridden_native_values():
    config = DockerRecipe(
        execution="envd_http",
        max_output_bytes_per_stream=32768,
        envd={"limits": {"max_output_bytes_per_stream": 4194304}},
    )
    assert config.launch_limits()["max_output_bytes_per_stream"] == 4194304
    with pytest.raises(ValidationError, match="Output preview"):
        DockerRecipe(execution="native", max_output_bytes_per_stream=32768)


@pytest.mark.parametrize("execution", ["native", "envd_http", "envd_ws"])
@pytest.mark.parametrize(
    "limits, legacy",
    [
        ({"max_spool_bytes": 128 * 1024 * 1024}, {}),
        ({"max_output_bytes_per_stream": 1024 * 1024}, {}),
        ({"max_process_records": 1, "max_device_processes": 1}, {"max_concurrent_processes": 1}),
    ],
)
def test_docker_partial_overrides_validate_against_legacy_fallbacks(execution, limits, legacy):
    config = DockerRecipe(execution=execution, envd={"limits": limits}, **legacy)
    assert config.envd.overrides() == {"limits": limits}
    frozen = config.model_dump_json()
    assert DockerRecipe.model_validate_json(frozen).envd.overrides() == {"limits": limits}
    effective = config.launch_limits()
    assert effective["max_spool_bytes"] >= 2 * effective["max_output_bytes_per_stream"]
    assert effective["max_output_preview_bytes"] <= effective["max_output_bytes_per_stream"]
    payload = envd_environment({"A13N_ENVD_CONFIG_JSON": json.dumps({"limits": effective})}, config.envd)
    assert json.loads(payload["A13N_ENVD_CONFIG_JSON"])["limits"] == effective
    # The daemon defaults remain the validation baseline for other providers.
    with pytest.raises(ValidationError):
        EnvdSettings.model_validate({"limits": limits})
