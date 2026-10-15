"""Native MXC acceptance tests. Set A13N_ENVD_TEST_BINARY on a Windows host.

An explicitly selected binary must pass: missing native PSEC support is a failure,
not a successful skip. Run the ignored Rust native_mxc_probe test first.
"""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from a13n_envd_client.eip.v1 import GrantAccess, RestrictedSandbox, SandboxGrant
from a13n_harness.environment.commands import CommandRequest, ShellCommand
from a13n_harness.environment.envd_policy import EnvdNetworkConfiguration
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment.local_envd.configuration import (
    LocalEnvdEnvironmentConfiguration,
    LocalEnvdLaunchConfiguration,
    local_envd_path,
)
from a13n_harness.providers.environment.local_envd.provider import LOCAL_ENVD
from a13n_harness_ui import sandbox


def test_windows_sandbox_selects_powershell_without_posix_paths(tmp_path, monkeypatch):
    shell = tmp_path / "pwsh.exe"
    shell.touch()
    monkeypatch.setattr(sandbox, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: str(shell) if name == "pwsh" else None)
    runtime = sandbox.create_sandbox_runtime(tmp_path / "envd.exe", roots=(tmp_path,))
    profile = runtime.configuration.shell_profiles[0]
    assert profile.executable == shell
    assert profile.fixed_arguments == ("-NoLogo", "-NoProfile", "-NonInteractive", "-Command")
    assert runtime.configuration.trusted_executable_roots == (tmp_path,)
    assert runtime.configuration.egress.mode == "deny"


@pytest.fixture
def binary():
    if sys.platform != "win32":
        pytest.skip("Native MXC acceptance requires Windows, not WSL")
    configured = os.environ.get("A13N_ENVD_TEST_BINARY")
    if configured is None:
        pytest.skip("Set A13N_ENVD_TEST_BINARY to require native MXC acceptance")
    return Path(configured).resolve(strict=True)


@pytest.mark.anyio
async def test_windows_production_preflight(binary, tmp_path):
    project = tmp_path / "project with 'quotes'"
    project.mkdir()
    await sandbox.validate_sandbox_runtime(binary, project)


@pytest.mark.anyio
async def test_windows_grants_commands_cleanup_and_replaced_root(binary, tmp_path, monkeypatch):
    project, readonly, private = (tmp_path / name for name in ("project", "readonly", "private"))
    for root in (project, readonly, private):
        root.mkdir()
    (readonly / "input").write_text("readable")
    secret = private / "secret"
    secret.write_text("not granted")
    monkeypatch.setenv("HOST_ONLY_SECRET", "must-not-be-inherited")
    owner = sandbox.create_sandbox_runtime(
        binary,
        roots=(project,),
        protected_roots=(private,),
        launch=LocalEnvdLaunchConfiguration(
            sandbox=RestrictedSandbox(
                mode="restricted",
                grants=(
                    SandboxGrant(
                        path=local_envd_path(readonly),
                        access=GrantAccess.READ_ONLY,
                    ),
                ),
            ),
            egress=EnvdNetworkConfiguration(mode="deny"),
        ),
    )
    connector = LOCAL_ENVD.execution_connector(
        LocalEnvdEnvironmentConfiguration(working_directory=local_envd_path(project)),
        runtime=owner,
        environment_id="windows-mxc-acceptance",
    )
    async with owner:
        environment = await connector.open()
        try:
            device = await owner.acquire_device()
            assert device.descriptor.boundary.backend == "windows_mxc"
            files, processes = environment.operations.files, environment.operations.processes
            assert files is not None and processes is not None
            assert (await files.read_text(local_envd_path(readonly / "input"))).text == "readable"
            for forbidden in (secret, private):
                with pytest.raises(EnvironmentError):
                    await files.read_text(local_envd_path(forbidden))
            with pytest.raises(EnvironmentError):
                await files.write_text(local_envd_path(readonly / "output"), "denied", mode="create")
            await files.write_text(local_envd_path(project / "written"), "persisted", mode="create")
            quoted = str(readonly / "output").replace("'", "''")
            result = await processes.exec(
                CommandRequest(
                    command=ShellCommand(
                        profile_id="default",
                        script=(
                            "if ($env:HOST_ONLY_SECRET) { exit 40 }; "
                            f"try {{ [IO.File]::WriteAllText('{quoted}', 'denied'); exit 41 }} catch {{}}; "
                            "[IO.File]::WriteAllText((Join-Path $env:TEMP 'private'), 'ok'); exit 0"
                        ),
                    )
                )
            )
            assert result.status.exit_code == 0
            await processes.start(
                CommandRequest(
                    command=ShellCommand(
                        profile_id="default",
                        script="while ($true) { [IO.File]::AppendAllText('heartbeat', 'x'); Start-Sleep -Milliseconds 50 }",
                    )
                )
            )
            async with asyncio.timeout(15):
                while not (project / "heartbeat").exists():
                    await asyncio.sleep(0.05)
        finally:
            await environment.close()
        size = (project / "heartbeat").stat().st_size
        await asyncio.sleep(0.3)
        assert (project / "heartbeat").stat().st_size == size
        assert (project / "written").read_text() == "persisted"
        # Closed workers release grant handles; a fresh object at the same path
        # must not inherit the Device's original authorization.
        project.rename(tmp_path / "original")
        project.mkdir()
        with pytest.raises(EnvironmentError):
            await connector.open()
