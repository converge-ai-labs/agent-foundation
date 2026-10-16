"""Native MXC acceptance tests. Set A13N_ENVD_TEST_BINARY on a Windows host.

An explicitly selected binary must pass: missing native PSEC support is a failure,
not a successful skip. Run the ignored Rust native_mxc_probe test first.
"""

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from a13n_envd_client.eip.v1 import GrantAccess, RestrictedSandbox, SandboxGrant
from a13n_harness.environment.commands import CommandRequest, ShellCommand
from a13n_harness.environment.envd_policy import EnvdNetworkConfiguration
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.files import FileQueryRequest, FileTextSearchRequest
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.retention import EnvironmentOutputPolicy
from a13n_harness.providers.environment.local_envd.configuration import (
    LocalEnvdEnvironmentConfiguration,
    LocalEnvdLaunchConfiguration,
    local_envd_path,
)
from a13n_harness.providers.environment.local_envd.provider import LOCAL_ENVD
from a13n_harness_ui import sandbox
from a13n_harness_ui.extensions import environment_adapters


def test_windows_sandbox_selects_powershell_without_posix_paths(tmp_path, monkeypatch):
    shell = tmp_path / "pwsh.exe"
    shell.touch()
    system = tmp_path / "windows" / "System32"
    tools = tmp_path / "tools"
    system.mkdir(parents=True)
    tools.mkdir()
    monkeypatch.setenv("SystemRoot", str(system.parent))
    monkeypatch.setenv("PATH", os.pathsep.join((str(tools), ".", str(tmp_path / "missing"), str(tools))))
    monkeypatch.setattr(sandbox, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(environment_adapters, "_host_shell", lambda: shell)
    runtime = sandbox.create_sandbox_runtime(tmp_path / "envd.exe", roots=(tmp_path,))
    profile = runtime.configuration.shell_profiles[0]
    assert profile.executable == shell
    assert profile.fixed_arguments == ("-NoLogo", "-NoProfile", "-NonInteractive", "-Command")
    assert runtime.configuration.trusted_executable_roots == (tmp_path, system, tools)
    assert runtime.configuration.egress.mode == "inherit"
    assert isinstance(runtime.configuration.sandbox, RestrictedSandbox)
    assert runtime.configuration.sandbox.full_disk_read
    strict = sandbox.create_sandbox_runtime(
        tmp_path / "envd.exe",
        roots=(tmp_path,),
        launch=LocalEnvdLaunchConfiguration(sandbox=RestrictedSandbox(mode="restricted", grants=())),
    )
    assert isinstance(strict.configuration.sandbox, RestrictedSandbox)
    assert not strict.configuration.sandbox.full_disk_read
    explicit = sandbox.create_sandbox_runtime(
        tmp_path / "envd.exe",
        roots=(tmp_path,),
        launch=LocalEnvdLaunchConfiguration(
            sandbox=RestrictedSandbox(mode="restricted", grants=()), trusted_executable_roots=(tools,)
        ),
    )
    assert explicit.configuration.trusted_executable_roots == (tools,)


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_profile_broad_read_default_is_windows_only_and_explicit_launch_stays_strict(monkeypatch, platform):
    monkeypatch.setattr(environment_adapters, "sys", SimpleNamespace(platform=platform))
    default = environment_adapters.LocalEnvdProfileConfiguration()
    assert isinstance(default.launch.sandbox, RestrictedSandbox)
    assert default.launch.sandbox.full_disk_read is (platform == "win32")
    explicit = environment_adapters.LocalEnvdProfileConfiguration.model_validate(
        {"launch": {"sandbox": {"mode": "restricted", "grants": []}}}
    )
    assert isinstance(explicit.launch.sandbox, RestrictedSandbox)
    assert not explicit.launch.sandbox.full_disk_read


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
async def test_windows_sandbox_resolves_host_tools_without_absolute_commands(binary, tmp_path, monkeypatch):
    assert shutil.which("git.exe"), "Native tool acceptance requires Git on the Host PATH"
    # Select the real test interpreter ahead of any Windows Store Python alias.
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    owner = sandbox.create_sandbox_runtime(binary, roots=(tmp_path,))
    connector = LOCAL_ENVD.execution_connector(
        LocalEnvdEnvironmentConfiguration(working_directory=local_envd_path(tmp_path)),
        runtime=owner,
        environment_id="windows-mxc-host-tools",
    )
    async with owner:
        environment = await connector.open()
        try:
            processes = environment.operations.processes
            assert processes is not None
            started = await processes.start(
                CommandRequest(
                    command=ShellCommand(
                        profile_id="default",
                        script=(
                            "$ErrorActionPreference = 'Stop'; "
                            "cmd.exe /d /c 'echo cmd-ready>cmd.txt'; if ($LASTEXITCODE -ne 0) { exit 40 }; "
                            "git.exe --version | Out-File -Encoding utf8 git.txt; "
                            "if ($LASTEXITCODE -ne 0) { exit 41 }; "
                            "python.exe -I -c \"from pathlib import Path; Path('python.txt').write_text('python-ready')\"; "
                            "if ($LASTEXITCODE -ne 0) { exit 42 }; exit 0"
                        ),
                    ),
                    output_policy=EnvironmentOutputPolicy(
                        max_inline_bytes=1024, max_output_bytes=4096, overflow="retain"
                    ),
                )
            )
            result = await processes.wait(started.process.handle, condition="tree_cleaned", timeout_seconds=15)
            assert result.status.exit_code == 0, repr(result)
            await processes.release(started.process.handle)
            assert (tmp_path / "cmd.txt").read_text().strip() == "cmd-ready"
            assert (tmp_path / "git.txt").read_text(encoding="utf-8-sig").startswith("git version ")
            assert (tmp_path / "python.txt").read_text() == "python-ready"
        finally:
            await environment.close()


@pytest.mark.anyio
async def test_windows_grants_commands_cleanup_and_replaced_root(binary, tmp_path, monkeypatch):
    project, readonly, private = (tmp_path / name for name in ("project", "readonly", "private"))
    for root in (project, readonly, private):
        root.mkdir()
    (readonly / "input").write_text("readable")
    secret = private / "secret"
    secret.write_text("not granted")
    monkeypatch.setenv("HOST_ONLY_SECRET", "must-not-be-inherited")
    allocations = tmp_path / "allocations"
    allocations.mkdir()
    owner = sandbox.create_sandbox_runtime(
        binary,
        roots=(project,),
        runtime_parent=allocations,
        protected_roots=(private,),
        launch=LocalEnvdLaunchConfiguration(
            sandbox=RestrictedSandbox(
                mode="restricted",
                full_disk_read=True,
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
            assert (await files.read_text(local_envd_path(secret))).text == "not granted"
            with pytest.raises(EnvironmentError):
                await files.write_text(local_envd_path(secret), "forbidden", mode="replace")
            with pytest.raises(EnvironmentError):
                await files.write_text(local_envd_path(readonly / "output"), "denied", mode="create")
            await files.write_text(local_envd_path(project / "written"), "persisted", mode="create")
            bootstrap = next(allocations.iterdir()) / "a13n-envd.json"
            assert bootstrap.is_file()
            control = bootstrap.parent / "envd-runtime" / "broker-only"
            control.write_text("private control state")
            for denied in (bootstrap, control):
                with pytest.raises(EnvironmentError):
                    await files.read_text(local_envd_path(denied))
            bootstrap_quoted = str(bootstrap).replace("'", "''")
            quoted = str(readonly / "output").replace("'", "''")
            started = await processes.start(
                CommandRequest(
                    output_policy=EnvironmentOutputPolicy(
                        max_inline_bytes=1024, max_output_bytes=4096, overflow="retain"
                    ),
                    command=ShellCommand(
                        profile_id="default",
                        script=(
                            "if ($env:HOST_ONLY_SECRET) { exit 40 }; "
                            f"try {{ [IO.File]::ReadAllText('{bootstrap_quoted}'); exit 43 }} catch {{}}; "
                            f"try {{ [IO.File]::WriteAllText('{quoted}', 'denied'); exit 41 }} catch {{}}; "
                            "[IO.File]::WriteAllText((Join-Path $env:TEMP 'private'), 'ok'); exit 0"
                        ),
                    ),
                )
            )
            result = await processes.wait(started.process.handle, condition="tree_cleaned", timeout_seconds=10)
            assert result.status.exit_code == 0
            await processes.release(started.process.handle)
            await processes.start(
                CommandRequest(
                    output_policy=EnvironmentOutputPolicy(
                        max_inline_bytes=1024, max_output_bytes=4096, overflow="retain"
                    ),
                    command=ShellCommand(
                        profile_id="default",
                        script="while ($true) { [IO.File]::AppendAllText('heartbeat', 'x'); Start-Sleep -Milliseconds 50 }",
                    ),
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
        with pytest.raises(EnvironmentProviderError):
            await connector.open()


@pytest.mark.anyio
async def test_windows_thread_files_readonly_project_and_file_operations(binary, tmp_path):
    project, thread = tmp_path / "project", tmp_path / "thread"
    project.mkdir()
    thread.mkdir()
    (project / "source.txt").write_text("project input")
    owner = sandbox.create_sandbox_runtime(
        binary,
        roots=(project, thread),
        thread_files_root=thread,
        project_access=GrantAccess.READ_ONLY,
    )
    attachment = thread / "attachments" / "input.txt"
    attachment.write_text("attachment input")
    work = thread / "tmp"
    junction = work / "escape"
    subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(project)], check=True, capture_output=True
    )
    connector = LOCAL_ENVD.execution_connector(
        LocalEnvdEnvironmentConfiguration(working_directory=local_envd_path(work)),
        runtime=owner,
        environment_id="windows-mxc-files",
    )
    async with owner:
        environment = await connector.open()
        try:
            files = environment.operations.files
            assert files is not None
            for readonly in (project / "source.txt", attachment):
                assert (await files.read_text(local_envd_path(readonly))).text.endswith("input")
                with pytest.raises(EnvironmentError):
                    await files.write_text(local_envd_path(readonly), "denied", mode="replace")
            with pytest.raises(EnvironmentError):
                await files.write_text(local_envd_path(junction / "source.txt"), "escaped", mode="replace")
            await files.remove(local_envd_path(junction), recursive=True)
            assert (project / "source.txt").read_text() == "project input"
            candidate, published = work / "candidate", work / "published"
            await files.mkdir(local_envd_path(candidate))
            await files.mkdir(local_envd_path(published))
            await files.copy(local_envd_path(attachment), local_envd_path(candidate / "input.txt"))
            await files.write_text(local_envd_path(published / "obsolete"), "old", mode="create")
            await files.move(local_envd_path(candidate), local_envd_path(published), replace=True)
            assert not candidate.exists() and not (published / "obsolete").exists()
            listing = await files.list(local_envd_path(published), max_results=10)
            assert [entry.path for entry in listing.entries] == [local_envd_path(published / "input.txt")]
            query = await files.query(FileQueryRequest(root=local_envd_path(work), pattern="**/*.txt", max_results=10))
            assert [entry.path for entry in query.entries] == [local_envd_path(published / "input.txt")]
            search = await files.search_text(
                FileTextSearchRequest(root=local_envd_path(work), pattern="attachment", max_matches=10)
            )
            assert [match.text for match in search.matches] == ["attachment input"]
            payload = bytes(range(256)) * 100

            async def chunks():
                yield payload[:123]
                yield payload[123:]

            await files.write_bytes_stream(local_envd_path(work / "binary.dat"), chunks(), mode="create")
            assert await files.read_bytes(local_envd_path(work / "binary.dat")) == payload
            await files.remove(local_envd_path(published), recursive=True)
            assert not published.exists()
        finally:
            await environment.close()


@pytest.mark.anyio
async def test_windows_broker_termination_stops_restricted_commands(binary, tmp_path, monkeypatch):
    created = []
    launch = asyncio.create_subprocess_exec

    async def record_broker(*args, **kwargs):
        process = await launch(*args, **kwargs)
        if "--config" in args:
            created.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", record_broker)
    owner = sandbox.create_sandbox_runtime(binary, roots=(tmp_path,))
    connector = LOCAL_ENVD.execution_connector(
        LocalEnvdEnvironmentConfiguration(working_directory=local_envd_path(tmp_path)),
        runtime=owner,
        environment_id="windows-mxc-broker-loss",
    )
    async with owner:
        environment = await connector.open()
        try:
            processes = environment.operations.processes
            assert processes is not None
            await processes.start(
                CommandRequest(
                    command=ShellCommand(
                        profile_id="default",
                        script="while ($true) { [IO.File]::AppendAllText('heartbeat', 'x'); Start-Sleep -Milliseconds 50 }",
                    ),
                    output_policy=EnvironmentOutputPolicy(
                        max_inline_bytes=1024, max_output_bytes=4096, overflow="retain"
                    ),
                )
            )
            heartbeat = tmp_path / "heartbeat"
            async with asyncio.timeout(15):
                while not heartbeat.exists():
                    await asyncio.sleep(0.05)
            assert len(created) == 1
            created[0].kill()
            await created[0].wait()
            # Allow kernel Job teardown, then observe before explicit owner close.
            await asyncio.sleep(0.3)
            size = heartbeat.stat().st_size
            await asyncio.sleep(0.3)
            assert heartbeat.stat().st_size == size
        finally:
            # Carrier loss is surfaced even though the kernel stopped the tree.
            with pytest.raises(EnvironmentProviderError):
                await environment.close()
