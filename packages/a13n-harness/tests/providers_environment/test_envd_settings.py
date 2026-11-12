"""Template startup settings preserve daemon semantics and legacy identities."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from a13n_envd_client import EIPDeviceConnection, StdioTransport
from a13n_envd_client.eip.v1 import SessionConfiguration
from a13n_envd_client.errors import EIPMethodError
from a13n_harness.environment.envd_settings import EnvdLimits, EnvdSettings
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.kubernetes.configuration import (
    KubernetesEnvironmentConfiguration,
    KubernetesState,
    decode,
    fingerprint,
)
from a13n_harness.providers.environment.kubernetes.provider import identity
from pydantic import ValidationError

FIXTURE = Path(__file__).resolve().parents[4] / "crates/a13n-envd/tests/fixtures/template-settings.json"


def test_defaults_and_cross_field_validation_match_daemon_fixture():
    fixture = json.loads(FIXTURE.read_text())
    limits = EnvdLimits()
    assert {name: getattr(limits, name) for name in EnvdLimits.model_fields} == fixture["defaults"]
    for case in fixture["cases"]:
        if case["valid"]:
            EnvdLimits.model_validate(case["limits"])
        else:
            with pytest.raises(ValidationError):
                EnvdLimits.model_validate(case["limits"])


def test_transfer_frame_schema_exposes_protocol_minimum():
    schema = EnvdLimits.model_json_schema()
    assert schema["properties"]["max_transfer_frame_bytes"]["minimum"] == 26


def test_only_explicit_values_survive_frozen_recipe_round_trip():
    assert EnvdSettings().model_dump() == {}
    assert EnvdSettings(limits=EnvdLimits()).model_dump() == {}
    for raw in [{"limits": {"max_sessions": 128}}, {"idle_timeout_ms": 3600000}, {"max_lifetime_ms": None}]:
        settings = EnvdSettings.model_validate(raw)
        assert json.loads(settings.model_dump_json()) == raw
        assert EnvdSettings.model_validate_json(settings.model_dump_json()).overrides() == raw
    assert "limits" in EnvdSettings.model_json_schema(mode="serialization")["properties"]


# Digests captured with the pre-Envd-settings fingerprint implementation.
@pytest.mark.parametrize(
    "inputs, legacy_fingerprint",
    [
        ({}, "09cc6ef3c0e7d51d6e7b96359cf511d0f9df868d54ce0eb2a511c5953a4bdf64"),
        ({"request_timeout_seconds": 30}, "c0f99ac3130fb9d0477863afed024a4d2a6d4041487efbc523718eb163a104c4"),
    ],
)
@pytest.mark.parametrize("empty_envd", [None, {}, {"limits": {}}])
def test_kubernetes_legacy_state_survives_omitted_and_empty_envd(inputs, legacy_fingerprint, empty_envd):
    values = {"execution_container": "envd", **inputs}
    if empty_envd is not None:
        values["envd"] = empty_envd
    configuration = KubernetesEnvironmentConfiguration.model_validate(values)
    assert fingerprint(configuration) == legacy_fingerprint
    state = KubernetesState(
        environment_id="env-existing",
        configuration_fingerprint=legacy_fingerprint,
        cluster="test",
        namespace="sandboxes",
        ownership_id="test-suite",
        pod_name="existing-pod",
        pod_uid="existing-pod-uid",
        profile_fingerprint="0" * 64,
        recipe_fingerprint="1" * 64,
    )
    assert decode(configuration, state.envelope()) == state
    assert identity(configuration=configuration, state=state.envelope()) == "existing-pod-uid"
    configured = configuration.model_copy(update={"envd": EnvdSettings(idle_timeout_ms=3000)})
    assert fingerprint(configured) != legacy_fingerprint
    with pytest.raises(EnvironmentProviderError) as error:
        decode(configured, state.envelope())
    assert error.value.code == "provider_target_conflict"
    configured_state = state.model_copy(update={"configuration_fingerprint": fingerprint(configured)})
    assert decode(configured, configured_state.envelope()) == configured_state


@pytest.mark.parametrize("value", [True, "10", 1.5, 0, -1, 2**64])
def test_limits_require_bounded_positive_integers(value):
    with pytest.raises(ValidationError):
        EnvdSettings.model_validate({"limits": {"max_sessions": value}})


@pytest.mark.parametrize("field", ["device_id", "transport", "execution", "full_control", "monitoring", "runtime_dir"])
def test_provider_owned_and_deferred_settings_are_not_template_inputs(field):
    with pytest.raises(ValidationError):
        EnvdSettings.model_validate({field: {}})


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform == "win32", reason="Exercises POSIX Session worker admission")
@pytest.mark.parametrize("configured", [False, True])
async def test_real_daemon_shared_session_capacity_and_release(tmp_path, configured):
    binary = os.environ.get("A13N_ENVD_TEST_BINARY")
    if not binary:
        pytest.skip("set A13N_ENVD_TEST_BINARY to exercise real Session reservation")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = EnvdSettings.model_validate({"limits": {"max_concurrent_file_transfers": 32}} if configured else {})
    process = await asyncio.create_subprocess_exec(
        binary,
        env={
            **{key: value for key, value in os.environ.items() if not key.startswith("A13N_ENVD_")},
            "A13N_ENVD_DEVICE_ID": "device-capacity-test",
            "A13N_ENVD_RUNTIME_DIR": str(runtime),
            "A13N_ENVD_TRANSPORT": "stdio",
            "A13N_ENVD_CONFIG_JSON": json.dumps(
                {
                    **settings.overrides(),
                    **(
                        {
                            "sandbox": {
                                "mode": "restricted",
                                "grants": [{"path": str(workspace), "access": "read_write"}],
                            }
                        }
                        if sys.platform == "darwin"
                        else {"execution": {"uid": os.getuid(), "gid": os.getgid(), "allow_sudo": False}}
                    ),
                }
            ),
        },
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    sessions = []
    device = None
    try:
        device = await EIPDeviceConnection.initialize(
            StdioTransport.from_process(process), expected_device_id="device-capacity-test"
        )
        count = 4 if configured else 2
        for _ in range(count):
            sessions.append(
                await device.open_session(configuration=SessionConfiguration(working_directory=str(workspace)))
            )
        with pytest.raises(EIPMethodError) as error:
            await device.open_session(configuration=SessionConfiguration(working_directory=str(workspace)))
        assert error.value.error.data.error_type.value == "busy"
        await asyncio.gather(*(session.close() for session in sessions))
        sessions.clear()
        # Closing all Sessions returns the reserved budgets to the Device.
        for _ in range(count):
            sessions.append(
                await device.open_session(configuration=SessionConfiguration(working_directory=str(workspace)))
            )
    finally:
        await asyncio.gather(*(session.close() for session in sessions))
        if device is not None:
            await device.close()
        if process.returncode is None:
            process.terminate()
        await asyncio.wait_for(process.communicate(), 10)
