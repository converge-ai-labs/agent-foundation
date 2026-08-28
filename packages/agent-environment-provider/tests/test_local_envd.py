from __future__ import annotations

import asyncio
import ctypes
import json
import os
import shutil
import sys
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from a13n_envd_client import EIPMethodError, EIPTransportClosedError
from a13n_envd_client import __version__ as envd_client_version
from a13n_environment_provider import (
    A13N_AGENT_ENVD_EXECUTABLE,
    EIPEnvironmentAttachment,
    EnvironmentManagementAction,
    EnvironmentOperationContext,
    EnvironmentPauseMode,
    EnvironmentProviderError,
    EnvironmentProviderSpec,
    EnvironmentReconciliationPhase,
    LocalEnvdEnvironmentProvider,
    LocalEnvdProviderConfiguration,
    LocalEnvdProviderRuntime,
    LocalEnvdProviderStateData,
    LocalEnvdResourcePhase,
    LocalEnvdWorkspaceConfiguration,
    TemporaryLocalEnvdRuntimeAllocator,
    build_environment_provider_factory_catalog,
    resolve_agent_envd_executable,
)
from a13n_environment_provider.local_envd import provider as local_envd_provider_module

pytestmark = pytest.mark.anyio

_FAKE_ENVD_EXECUTABLES: set[Path] = set()


@pytest.fixture(autouse=True)
def _launch_fake_envd_scripts_with_python_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name != "nt":
        return
    create_subprocess_exec = asyncio.create_subprocess_exec

    async def launch(
        program: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *arguments: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        **options: Any,
    ) -> asyncio.subprocess.Process:
        candidate = Path(os.fsdecode(program)).resolve()
        if candidate in _FAKE_ENVD_EXECUTABLES:
            return await create_subprocess_exec(
                sys.executable,
                str(candidate),
                *arguments,
                **options,
            )
        return await create_subprocess_exec(program, *arguments, **options)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)


def _operation(
    action: EnvironmentManagementAction,
    suffix: str,
    *,
    correlation: str = "resource-local-envd-1",
) -> EnvironmentOperationContext:
    return EnvironmentOperationContext(
        operation_id=f"operation-{suffix}",
        action=action,
        resource_correlation=correlation,
        attempt=1,
    )


def _write_fake_envd(
    path: Path,
    *,
    version: str = envd_client_version,
    probe_ready: bool = True,
    readiness_ready: bool = True,
    exit_after_ready: bool = False,
    exit_before_close_response: int | None = None,
    spawn_child_before_close_response: int | None = None,
    child_pid_file: Path | None = None,
) -> Path:
    script = f"""#!{sys.executable}
import json
import os
import subprocess
import sys
from pathlib import Path


def read_request():
    content_length = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line == b"\\r\\n":
            break
        name, value = line.decode("ascii").split(":", 1)
        if name.lower() == "content-length":
            content_length = int(value.strip())
    if content_length is None:
        raise SystemExit(3)
    payload = sys.stdin.buffer.read(content_length)
    if len(payload) != content_length:
        return None
    return json.loads(payload)


def write_response(request_id, result):
    payload = json.dumps(
        {{"jsonrpc": "2.0", "id": request_id, "result": result}},
        separators=(",", ":"),
    ).encode()
    sys.stdout.buffer.write(b"Content-Length: " + str(len(payload)).encode() + b"\\r\\n\\r\\n" + payload)
    sys.stdout.buffer.flush()


if sys.argv[1:] == ["--version"]:
    sys.stdout.buffer.write(b"agent-envd {version}\\n")
    raise SystemExit(0)
if sys.argv[1:] == ["isolation", "probe", "--json"]:
    json.dump({{
        "ready": {probe_ready!r},
        "isolation": True,
        "backend": "test",
        "network_isolation": True,
        "filesystem_containment": True,
        "process_containment": True,
        "cleanup": "test",
    }}, sys.stdout)
    raise SystemExit(0)
if len(sys.argv) == 3 and sys.argv[1] == "--config":
    with open(sys.argv[2], encoding="utf-8") as stream:
        json.load(stream)
    if "AGENT_ENVD_READY_FILE" in os.environ:
        raise SystemExit(5)
    environment_id = os.environ["AGENT_ENVD_ENVIRONMENT_ID"]
    descriptor = {{
        "environment_id": environment_id,
        "generation": 1,
        "available_methods": ["environment.readiness", "session.close"],
        "limits": {{
            "max_request_bytes": 16777216,
            "max_response_bytes": 16777216,
            "max_concurrent_operations": 4,
            "max_processes": 1,
            "max_operation_duration_ms": 10000,
            "max_output_preview_bytes": 1,
            "max_output_bytes_per_stream": 1,
            "max_transfer_frame_bytes": 4194304,
            "max_concurrent_file_transfers": 1,
            "max_file_transfer_bytes": 1,
        }},
        "isolation": {{
            "mode": "disabled",
            "backend": "outer_host",
            "filesystem_containment": False,
            "process_containment": False,
            "network_containment": False,
            "network_policy": "host",
            "cleanup_guarantee": "outer_host",
        }},
        "execution_features": {{
            "process_count_limit": False,
            "memory_bytes_limit": False,
            "cpu_time_limit": False,
            "per_command_network_deny": False,
            "signal_interrupt": False,
            "signal_terminate": False,
        }},
    }}
    close_count = 0
    while request := read_request():
        method = request["method"]
        if method == "initialize":
            write_response(request["id"], {{
                "protocol_version": "0.1",
                "server": {{"name": "agent-envd", "version": "{version}"}},
                "descriptor": descriptor,
            }})
        elif method == "environment.readiness":
            write_response(request["id"], {{
                "ready": {readiness_ready!r},
                "environment_id": environment_id,
                "generation": 1,
            }})
            if {exit_after_ready!r}:
                os._exit(0)
        elif method == "session.close":
            close_count += 1
            if close_count == {spawn_child_before_close_response!r}:
                child = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                Path({str(child_pid_file) if child_pid_file is not None else None!r}).write_text(str(child.pid))
            if close_count == {exit_before_close_response!r}:
                os._exit(0)
            write_response(request["id"], {{"closed": True}})
        else:
            raise SystemExit(4)
    raise SystemExit(0)
raise SystemExit(2)
"""
    path.write_text(script)
    path.chmod(0o755)
    resolved = path.resolve()
    _FAKE_ENVD_EXECUTABLES.add(resolved)
    return resolved


def _provider(
    workspace: Path,
    executable: Path,
    allocator: Callable[[], AbstractAsyncContextManager[Path]],
    *,
    read_only: bool = False,
) -> LocalEnvdEnvironmentProvider:
    catalog = build_environment_provider_factory_catalog(builtin_keys=("a13n.local-envd",))
    provider = catalog.create_provider(
        EnvironmentProviderSpec(
            provider_key="a13n.local-envd",
            schema_version="1",
            parameters={
                "environment_id": "local-envd-1",
                "workspace": {
                    "path": str(workspace),
                    "read_only": read_only,
                },
            },
        ),
        runtime=LocalEnvdProviderRuntime(
            executable=executable,
            allocate_private_runtime=allocator,
        ),
    )
    assert isinstance(provider, LocalEnvdEnvironmentProvider)
    return provider


def test_resolve_agent_envd_executable_uses_explicit_env_then_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    explicit = _write_fake_envd(tmp_path / "explicit-envd")
    configured = _write_fake_envd(tmp_path / "configured-envd")
    discovered = _write_fake_envd(tmp_path / "discovered-envd")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(A13N_AGENT_ENVD_EXECUTABLE, str(configured))
    monkeypatch.setattr(shutil, "which", lambda _name: str(discovered))

    assert resolve_agent_envd_executable(explicit.name) == explicit
    alias = tmp_path / "agent-envd-alias"
    try:
        alias.symlink_to(explicit)
    except OSError:
        pass
    else:
        assert resolve_agent_envd_executable(alias.name) == alias.absolute()
    assert resolve_agent_envd_executable() == configured

    monkeypatch.delenv(A13N_AGENT_ENVD_EXECUTABLE)
    assert resolve_agent_envd_executable() == discovered


def test_local_envd_normalizes_python_rc_version_to_canonical_release_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_envd_provider_module, "envd_client_version", "1.2.3rc4")
    assert local_envd_provider_module._client_release_identity() == "1.2.3-rc.4"


def test_resolve_agent_envd_executable_rejects_missing_selection(tmp_path: Path) -> None:
    with pytest.raises(EnvironmentProviderError) as exc_info:
        resolve_agent_envd_executable(tmp_path / "missing")
    assert exc_info.value.code == "provider_runtime_invalid"


def test_local_envd_configuration_validates_bounded_limits_and_absolute_paths(
    tmp_path: Path,
) -> None:
    configuration = LocalEnvdProviderConfiguration(
        environment_id="local-envd-1",
        workspace=LocalEnvdWorkspaceConfiguration(path=tmp_path),
    )
    assert configuration.workspace.path == tmp_path

    with pytest.raises(ValueError):
        LocalEnvdProviderConfiguration(
            environment_id="local-envd-1",
            workspace=LocalEnvdWorkspaceConfiguration(path=tmp_path),
            max_output_bytes_per_stream=1024,
            max_spool_bytes=1024,
        )
    with pytest.raises(ValueError):
        LocalEnvdWorkspaceConfiguration(path=Path("relative"))


async def test_local_envd_rejects_mismatched_release_and_failed_isolation_without_allocating(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    allocation_attempted = False

    @asynccontextmanager
    async def allocate() -> AsyncGenerator[Path]:
        nonlocal allocation_attempted
        allocation_attempted = True
        raise AssertionError("validation failure must not allocate a runtime")
        yield tmp_path

    cases = (
        (
            _write_fake_envd(tmp_path / "wrong-version-envd", version="9.9.9"),
            "release does not match",
        ),
        (
            _write_fake_envd(tmp_path / "failed-probe-envd", probe_ready=False),
            "required isolation probe did not report production readiness",
        ),
    )
    for index, (executable, expected_error) in enumerate(cases):
        provider = _provider(workspace, executable, allocate)
        reconciled = await provider.reconcile(
            _operation(EnvironmentManagementAction.CREATE, f"reconcile-invalid-runtime-{index}"),
            last_known_state=None,
        )
        assert reconciled.phase is EnvironmentReconciliationPhase.UNKNOWN
        assert reconciled.evidence == {"reason": "runtime_compatibility_unconfirmed"}
        with pytest.raises(EnvironmentProviderError) as exc_info:
            await provider.create(
                operation=_operation(
                    EnvironmentManagementAction.CREATE,
                    f"invalid-runtime-{index}",
                )
            )
        assert exc_info.value.code == "provider_unavailable"
        assert expected_error in str(exc_info.value)
    assert not allocation_attempted


async def test_local_envd_resource_writes_strict_config_and_reuses_one_carrier(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executable = _write_fake_envd(tmp_path / "agent-envd")
    allocation = tmp_path / "allocation"

    @asynccontextmanager
    async def allocate() -> AsyncGenerator[Path]:
        allocation.mkdir()
        try:
            yield allocation
        finally:
            shutil.rmtree(allocation, ignore_errors=True)

    provider = _provider(workspace, executable, allocate)
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE, "create"))
    assert not allocation.exists()
    state_data = LocalEnvdProviderStateData.model_validate(resource.state.data)
    assert state_data.phase is LocalEnvdResourcePhase.RUNNING
    assert state_data.resource_correlation == "resource-local-envd-1"

    async with resource:
        bootstrap = json.loads((allocation / "agent-envd.json").read_text())
        assert bootstrap["root_mount_id"] == "workspace"
        assert bootstrap["execution"] == {
            "extra_read_only_paths": [],
            "isolation": "required",
            "network": "host",
        }
        mount = bootstrap["mounts"][0]
        assert mount["native_root"] == str(workspace.resolve())
        assert mount["allowed_operations"] == [
            "stat",
            "read_text",
            "open_reader",
            "list",
            "find",
            "search",
            "write_text",
            "open_writer",
            "remove",
            "move",
        ]

        async with resource.acquire_attachment() as first:
            assert isinstance(first, EIPEnvironmentAttachment)
        async with resource.acquire_attachment() as second:
            assert isinstance(second, EIPEnvironmentAttachment)
        assert first.attachment_id != second.attachment_id
        running_state = resource.state

    assert not allocation.exists()
    await provider.destroy(
        running_state,
        operation=_operation(EnvironmentManagementAction.DESTROY, "destroy"),
    )


async def test_local_envd_entry_rejects_false_eip_readiness(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime_parent = tmp_path / "runtimes"
    workspace.mkdir()
    runtime_parent.mkdir()
    provider = _provider(
        workspace,
        _write_fake_envd(tmp_path / "agent-envd", readiness_ready=False),
        TemporaryLocalEnvdRuntimeAllocator(parent=runtime_parent),
    )
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE, "create"))

    with pytest.raises(EnvironmentProviderError) as exc_info:
        async with resource:
            raise AssertionError("a not-ready daemon must not enter a Resource")
    assert exc_info.value.code == "provider_unavailable"
    assert not tuple(runtime_parent.iterdir())


async def test_local_envd_entry_rejects_daemon_that_exits_after_readiness(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime_parent = tmp_path / "runtimes"
    workspace.mkdir()
    runtime_parent.mkdir()
    provider = _provider(
        workspace,
        _write_fake_envd(tmp_path / "agent-envd", exit_after_ready=True),
        TemporaryLocalEnvdRuntimeAllocator(parent=runtime_parent),
    )
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE, "create"))

    with pytest.raises(EnvironmentProviderError) as exc_info:
        async with resource:
            raise AssertionError("an exited daemon must not enter a Resource")
    assert exc_info.value.code == "provider_unavailable"
    assert not tuple(runtime_parent.iterdir())


async def test_local_envd_close_response_loss_fences_carrier(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime_parent = tmp_path / "runtimes"
    workspace.mkdir()
    runtime_parent.mkdir()
    provider = _provider(
        workspace,
        _write_fake_envd(
            tmp_path / "agent-envd",
            exit_before_close_response=2,
        ),
        TemporaryLocalEnvdRuntimeAllocator(parent=runtime_parent),
    )
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE, "create"))

    async with resource:
        with pytest.raises(EIPTransportClosedError):
            async with resource.acquire_attachment() as attachment:
                assert isinstance(attachment, EIPEnvironmentAttachment)
                async with attachment.session_source.open_session(
                    expected_environment_id="local-envd-1",
                    required_methods=frozenset({"environment.readiness", "session.close"}),
                ):
                    pass
        with pytest.raises(EnvironmentProviderError) as unavailable:
            async with resource.acquire_attachment():
                raise AssertionError("a close-ambiguous carrier must not be reused")
        assert unavailable.value.code == "provider_unavailable"

    assert not tuple(runtime_parent.iterdir())


async def test_local_envd_process_exit_after_admission_closes_attachment_admission(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    runtime_parent = tmp_path / "runtimes"
    workspace.mkdir()
    runtime_parent.mkdir()
    provider = _provider(
        workspace,
        _write_fake_envd(tmp_path / "agent-envd"),
        TemporaryLocalEnvdRuntimeAllocator(parent=runtime_parent),
    )
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE, "create"))

    async with resource:
        process = resource._process
        assert process is not None
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=5)
        with pytest.raises(EnvironmentProviderError) as unavailable:
            async with resource.acquire_attachment():
                raise AssertionError("an exited daemon must not issue an attachment")
        assert unavailable.value.code == "provider_unavailable"

    assert not tuple(runtime_parent.iterdir())


async def test_local_envd_cleanup_reports_all_uncertain_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    allocation = tmp_path / "allocation"
    workspace.mkdir()
    release_attempted = False

    @asynccontextmanager
    async def allocate() -> AsyncGenerator[Path]:
        nonlocal release_attempted
        allocation.mkdir()
        try:
            yield allocation
        finally:
            release_attempted = True
            shutil.rmtree(allocation)
            raise OSError("forced allocator cleanup failure")

    provider = _provider(workspace, _write_fake_envd(tmp_path / "agent-envd"), allocate)
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE, "create"))
    await resource.__aenter__()
    carrier = resource._carrier
    assert carrier is not None
    close = carrier.close
    terminate_process_tree = local_envd_provider_module._terminate_process_tree
    carrier_close_attempted = False
    process_cleanup_attempted = False

    async def close_then_fail() -> None:
        nonlocal carrier_close_attempted
        carrier_close_attempted = True
        await close()
        raise OSError("forced carrier cleanup failure")

    async def terminate_then_fail(process: asyncio.subprocess.Process) -> None:
        nonlocal process_cleanup_attempted
        process_cleanup_attempted = True
        await terminate_process_tree(process)
        raise OSError("forced process cleanup failure")

    monkeypatch.setattr(carrier, "close", close_then_fail)
    monkeypatch.setattr(local_envd_provider_module, "_terminate_process_tree", terminate_then_fail)

    with pytest.raises(EnvironmentProviderError) as cleanup:
        await resource.__aexit__(None, None, None)

    assert cleanup.value.code == "provider_cleanup_failed"
    assert cleanup.value.details == {"failure_count": 3}
    notes = "\n".join(cleanup.value.__notes__)
    assert "forced carrier cleanup failure" in notes
    assert "forced process cleanup failure" in notes
    assert "forced allocator cleanup failure" in notes
    assert carrier_close_attempted
    assert process_cleanup_attempted
    assert release_attempted
    assert not allocation.exists()


async def test_local_envd_resource_exit_defers_cancellation_until_cleanup_finishes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    allocation = tmp_path / "allocation"
    workspace.mkdir()
    release_started = asyncio.Event()
    allow_release = asyncio.Event()

    @asynccontextmanager
    async def allocate() -> AsyncGenerator[Path]:
        allocation.mkdir()
        try:
            yield allocation
        finally:
            release_started.set()
            await allow_release.wait()
            shutil.rmtree(allocation)

    provider = _provider(workspace, _write_fake_envd(tmp_path / "agent-envd"), allocate)
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE, "create"))
    await resource.__aenter__()
    exiting = asyncio.create_task(resource.__aexit__(None, None, None))
    await release_started.wait()

    exiting.cancel()
    await asyncio.sleep(0)
    assert not exiting.done()
    assert allocation.exists()

    allow_release.set()
    with pytest.raises(asyncio.CancelledError):
        await exiting
    assert not allocation.exists()


async def test_local_envd_state_correlation_and_resume_phase_are_exact(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime_parent = tmp_path / "runtimes"
    workspace.mkdir()
    runtime_parent.mkdir()
    provider = _provider(
        workspace,
        _write_fake_envd(tmp_path / "agent-envd"),
        TemporaryLocalEnvdRuntimeAllocator(parent=runtime_parent),
    )
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE, "create"))
    async with resource:
        running = resource.state
    resumed = await provider.resume(
        running,
        operation=_operation(EnvironmentManagementAction.RESUME, "resume-running"),
    )
    async with resumed:
        assert LocalEnvdProviderStateData.model_validate(resumed.state.data).phase is LocalEnvdResourcePhase.RUNNING

    with pytest.raises(EnvironmentProviderError) as destroy_error:
        await provider.destroy(
            resource.state,
            operation=_operation(
                EnvironmentManagementAction.DESTROY,
                "destroy-other",
                correlation="resource-other",
            ),
        )
    assert destroy_error.value.code == "provider_state_invalid"

    await provider.destroy(
        resource.state,
        operation=_operation(EnvironmentManagementAction.DESTROY, "destroy"),
    )


async def test_local_envd_filesystem_pause_cleans_entry_and_resume_starts_fresh_entry(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    runtime_parent = tmp_path / "runtimes"
    workspace.mkdir()
    runtime_parent.mkdir()
    executable = _write_fake_envd(tmp_path / "agent-envd")
    allocator = TemporaryLocalEnvdRuntimeAllocator(parent=runtime_parent)
    provider = _provider(workspace, executable, allocator)
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE, "create"))

    async with resource:
        pause_operation = _operation(EnvironmentManagementAction.PAUSE, "pause")
        paused = await provider.pause(
            resource,
            operation=pause_operation,
            mode=EnvironmentPauseMode.FILESYSTEM,
        )
        assert LocalEnvdProviderStateData.model_validate(paused.data).phase is LocalEnvdResourcePhase.PAUSED
        assert (
            await provider.pause(
                resource,
                operation=pause_operation,
                mode=EnvironmentPauseMode.FILESYSTEM,
            )
            == paused
        )
        assert not tuple(runtime_parent.iterdir())

    with pytest.raises(EnvironmentProviderError) as pause_error:
        await provider.pause(
            resource,
            operation=pause_operation,
            mode=EnvironmentPauseMode.FILESYSTEM,
        )
    assert pause_error.value.code == "provider_attachment_conflict"

    resumed = await provider.resume(
        paused,
        operation=_operation(EnvironmentManagementAction.RESUME, "resume"),
    )
    async with resumed:
        assert tuple(runtime_parent.iterdir())
        running = resumed.state
    assert not tuple(runtime_parent.iterdir())

    await provider.destroy(
        running,
        operation=_operation(EnvironmentManagementAction.DESTROY, "destroy"),
    )
    assert workspace.is_dir()


async def test_local_envd_rejects_pause_with_active_attachment(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime_parent = tmp_path / "runtimes"
    workspace.mkdir()
    runtime_parent.mkdir()
    provider = _provider(
        workspace,
        _write_fake_envd(tmp_path / "agent-envd"),
        TemporaryLocalEnvdRuntimeAllocator(parent=runtime_parent),
    )
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE, "create"))

    async with resource:
        async with resource.acquire_attachment():
            with pytest.raises(EnvironmentProviderError) as exc_info:
                await provider.pause(
                    resource,
                    operation=_operation(EnvironmentManagementAction.PAUSE, "pause"),
                    mode=EnvironmentPauseMode.FILESYSTEM,
                )
            assert exc_info.value.code == "provider_conflict"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups are required")
async def test_local_envd_cleanup_terminates_descendants_after_leader_exit(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    leader = tmp_path / "leader.py"
    leader.write_text(
        "\n".join(
            (
                "import subprocess",
                "import sys",
                "from pathlib import Path",
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)",
                f"Path({str(pid_file)!r}).write_text(str(child.pid))",
            )
        )
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(leader),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    await process.wait()
    child_pid = int(pid_file.read_text())
    assert local_envd_provider_module._posix_process_group_exists(process.pid)

    await local_envd_provider_module._terminate_process_tree(process)

    assert not local_envd_provider_module._posix_process_group_exists(process.pid)
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are required")
async def test_local_envd_windows_job_terminates_descendant_after_daemon_exit(tmp_path: Path) -> None:
    from ctypes import wintypes

    workspace = tmp_path / "workspace"
    runtime_parent = tmp_path / "runtimes"
    child_pid_file = tmp_path / "child.pid"
    workspace.mkdir()
    runtime_parent.mkdir()
    provider = _provider(
        workspace,
        _write_fake_envd(
            tmp_path / "agent-envd",
            exit_before_close_response=2,
            spawn_child_before_close_response=2,
            child_pid_file=child_pid_file,
        ),
        TemporaryLocalEnvdRuntimeAllocator(parent=runtime_parent),
    )
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE, "create"))
    await resource.__aenter__()
    process_handle = None
    try:
        with pytest.raises(EIPTransportClosedError):
            async with resource.acquire_attachment() as attachment:
                assert isinstance(attachment, EIPEnvironmentAttachment)
                async with attachment.session_source.open_session(
                    expected_environment_id="local-envd-1",
                    required_methods=frozenset({"environment.readiness", "session.close"}),
                ):
                    pass
        child_pid = int(child_pid_file.read_text())
        job = resource._windows_job
        assert job is not None
        assert await asyncio.to_thread(job.active_process_count) >= 1

        windows_ctypes = cast(Any, ctypes)
        kernel32 = windows_ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        open_process.restype = wintypes.HANDLE
        process_handle = open_process(0x00100000, False, child_pid)
        assert process_handle
        wait_for_single_object = kernel32.WaitForSingleObject
        wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        wait_for_single_object.restype = wintypes.DWORD
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
    finally:
        await resource.__aexit__(None, None, None)

    try:
        assert wait_for_single_object(process_handle, 5000) == 0
    finally:
        assert close_handle(process_handle)
    assert not tuple(runtime_parent.iterdir())


async def test_local_envd_real_daemon_fences_carrier_after_failed_initialization(
    tmp_path: Path,
) -> None:
    configured = os.environ.get(A13N_AGENT_ENVD_EXECUTABLE)
    if configured is None:
        pytest.skip(f"{A13N_AGENT_ENVD_EXECUTABLE} is not configured")
    workspace = tmp_path / "workspace"
    runtime_parent = tmp_path / "runtimes"
    workspace.mkdir()
    runtime_parent.mkdir()
    provider = _provider(
        workspace,
        resolve_agent_envd_executable(configured),
        TemporaryLocalEnvdRuntimeAllocator(parent=runtime_parent),
    )
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE, "create"))

    async with resource:
        with pytest.raises(EIPMethodError):
            async with resource.acquire_attachment() as attachment:
                assert isinstance(attachment, EIPEnvironmentAttachment)
                async with attachment.session_source.open_session(
                    expected_environment_id="wrong-environment",
                    required_methods=frozenset({"session.close"}),
                ):
                    raise AssertionError("incompatible initialization must not open a session")
        with pytest.raises(EnvironmentProviderError) as exc_info:
            async with resource.acquire_attachment():
                raise AssertionError("a fenced carrier must not issue another attachment")
        assert exc_info.value.code == "provider_unavailable"
        state = resource.state

    await provider.destroy(
        state,
        operation=_operation(EnvironmentManagementAction.DESTROY, "destroy"),
    )


async def test_local_envd_real_daemon_supports_sequential_sessions(tmp_path: Path) -> None:
    configured = os.environ.get(A13N_AGENT_ENVD_EXECUTABLE)
    if configured is None:
        pytest.skip(f"{A13N_AGENT_ENVD_EXECUTABLE} is not configured")
    executable = resolve_agent_envd_executable(configured)
    workspace = tmp_path / "workspace"
    runtime_parent = tmp_path / "runtimes"
    workspace.mkdir()
    runtime_parent.mkdir()
    provider = _provider(
        workspace,
        executable,
        TemporaryLocalEnvdRuntimeAllocator(parent=runtime_parent),
    )
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE, "create"))

    async with resource:
        generations = []
        for _index in range(2):
            async with resource.acquire_attachment() as attachment:
                assert isinstance(attachment, EIPEnvironmentAttachment)
                async with attachment.session_source.open_session(
                    expected_environment_id="local-envd-1",
                    required_methods=frozenset({"environment.describe", "file.stat", "session.close"}),
                ) as session:
                    generations.append(session.generation)
                    assert session.descriptor.environment_id == "local-envd-1"
        assert generations[0] == generations[1]
        state = resource.state

    await provider.destroy(
        state,
        operation=_operation(EnvironmentManagementAction.DESTROY, "destroy"),
    )
    assert not tuple(runtime_parent.iterdir())
