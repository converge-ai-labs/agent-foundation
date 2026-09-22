"""Launch authority stays fixed; Session credential values stay runtime-only."""

import json
from pathlib import Path

import pytest
from a13n_envd_client import EIPSessionStateError
from a13n_envd_client.eip.v1 import (
    DisabledSandbox,
    EgressMode,
    ExecutionBackend,
    ExecutionBoundary,
    GrantAccess,
    RestrictedSandbox,
    SandboxGrant,
)
from a13n_harness.providers.environment.envd_policy import (
    EnvdBoundaryRequirement,
    EnvdEgressConfiguration,
    EnvironmentEnvdCredentialResolver,
    resolve_egress,
)
from a13n_harness.providers.environment.local_envd._daemon import _daemon_environment, _write_private_bootstrap
from a13n_harness.providers.environment.local_envd.configuration import LocalEnvdLaunchConfiguration
from pydantic import ValidationError

pytestmark = pytest.mark.anyio


def test_unused_schema_is_replaced_not_accepted_as_aliases():
    for value in ({"egress": {}}, {"egress": {"enabled": True}}, {"sandbox": {"enabled": True}}):
        with pytest.raises(ValidationError):
            LocalEnvdLaunchConfiguration.model_validate(value)
    for value in ({}, {"allow_hosts": []}, {"destinations": {"mode": "public"}, "unrestricted": True}):
        with pytest.raises(ValidationError):
            EnvdEgressConfiguration.model_validate(value)
    with pytest.raises(ValidationError, match="configured together"):
        LocalEnvdLaunchConfiguration.model_validate({"execution": {"uid": 1000}})


async def test_credential_references_are_resolved_afresh_and_never_in_launch(tmp_path, monkeypatch):
    configuration = EnvdEgressConfiguration.model_validate(
        {
            "destinations": {"mode": "allowlist", "hosts": ["api.example.com"]},
            "secrets": [
                {
                    "env": "PAYLOAD_TOKEN",
                    "source": {"kind": "environment", "name": "HOST_CREDENTIAL"},
                    "inject_hosts": ["api.example.com"],
                }
            ],
        }
    )
    resolver = EnvironmentEnvdCredentialResolver()
    monkeypatch.delenv("HOST_CREDENTIAL", raising=False)
    with pytest.raises(ValueError, match="unavailable"):
        await resolve_egress(configuration, resolver)
    monkeypatch.setenv("HOST_CREDENTIAL", "test-only-first-token")
    first = await resolve_egress(configuration, resolver)
    assert first is not None and first.secrets[0].value == "test-only-first-token"
    assert first.secrets[0].env == "PAYLOAD_TOKEN"
    monkeypatch.setenv("HOST_CREDENTIAL", "test-only-rotated-token")
    second = await resolve_egress(configuration, resolver)
    assert second is not None and second.secrets[0].value == "test-only-rotated-token"
    assert "test-only" not in configuration.model_dump_json()
    assert "test-only" not in repr(second)
    launch = LocalEnvdLaunchConfiguration.model_validate(
        {
            "default_working_directory": str(tmp_path),
            "egress": {"mode": "controlled"},
        }
    )
    runtime, config_file, _ = _write_private_bootstrap(tmp_path, launch)
    assert "HOST_CREDENTIAL" not in _daemon_environment(
        device_id="device-test", runtime_dir=runtime, configuration=launch
    )
    assert "PAYLOAD_TOKEN" not in config_file.read_text()
    assert "test-only" not in config_file.read_text()
    assert json.loads(config_file.read_text())["egress"] == {"mode": "controlled"}


def test_boundary_requirement_checks_canonical_grants_not_only_mode():
    sandbox = RestrictedSandbox(
        mode="restricted", grants=(SandboxGrant(path="/workspace", access=GrantAccess.READ_ONLY),)
    )
    requirement = EnvdBoundaryRequirement(sandbox=sandbox, egress=EgressMode.DENY, privilege_gain_blocked=True)
    boundary = ExecutionBoundary(
        sandbox=sandbox,
        egress=EgressMode.DENY,
        privilege_gain_blocked=True,
        backend=ExecutionBackend.LINUX_BUBBLEWRAP,
        policy_digest="a" * 64,
    )
    requirement.check(boundary)
    for update in (
        {"egress": EgressMode.INHERIT},
        {"sandbox": DisabledSandbox(mode="disabled")},
        {
            "sandbox": RestrictedSandbox(
                mode="restricted", grants=(SandboxGrant(path="/workspace", access=GrantAccess.READ_WRITE),)
            )
        },
        {"privilege_gain_blocked": False},
    ):
        with pytest.raises(EIPSessionStateError, match="boundary"):
            requirement.check(boundary.model_copy(update=update))


def test_native_launch_keeps_inherited_environment(monkeypatch):
    monkeypatch.setenv("NATIVE_TEST_VARIABLE", "kept")
    environment = _daemon_environment(
        device_id="device-test", runtime_dir=Path("/runtime"), configuration=LocalEnvdLaunchConfiguration()
    )
    assert environment["NATIVE_TEST_VARIABLE"] == "kept"
