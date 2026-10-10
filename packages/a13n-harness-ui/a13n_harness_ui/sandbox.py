"""Host grant selection; Envd owns the complete Session execution boundary."""

from __future__ import annotations

import asyncio
import os
import shlex
import sys
import tempfile
from pathlib import Path

from a13n_envd_client.eip.v1 import GrantAccess, RestrictedSandbox, SandboxGrant
from a13n_environment.commands import CommandRequest, ShellCommand
from a13n_environment.envd_policy import EnvdNetworkConfiguration
from a13n_environment.local_envd.configuration import (
    LocalEnvdEnvironmentConfiguration,
    LocalEnvdLaunchConfiguration,
    LocalEnvdShellProfile,
)
from a13n_environment.local_envd.provider import LOCAL_ENVD
from a13n_environment.local_envd.runtime import (
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
)
from a13n_environment.retention import EnvironmentOutputPolicy
from anyio import CancelScope, to_thread

from a13n_harness_ui.errors import EnvironmentLifecycleError


def create_sandbox_runtime(
    executable: Path,
    *,
    roots: tuple[Path, ...],
    runtime_parent: Path | None = None,
    protected_roots: tuple[Path, ...] = (),
    thread_files_root: Path | None = None,
    launch: LocalEnvdLaunchConfiguration | None = None,
    project_access: GrantAccess = GrantAccess.READ_WRITE,
) -> LocalEnvdProviderRuntime:
    """Build an inert Device owner; broad roots cannot expose Host private state."""
    if sys.platform not in {"linux", "darwin"}:
        raise _unavailable("Local Sandbox is not available on this platform.")
    canonical = tuple(dict.fromkeys(root.resolve(strict=True) for root in roots))
    if not canonical or any(not root.is_dir() for root in canonical):
        raise _unavailable("Sandbox roots must be existing directories.")
    launch = launch or LocalEnvdLaunchConfiguration(
        sandbox=RestrictedSandbox(mode="restricted", grants=()),
        egress=EnvdNetworkConfiguration(mode="deny"),
    )
    grants = []
    thread_root = None if thread_files_root is None else thread_files_root.resolve(strict=True)
    if isinstance(launch.sandbox, RestrictedSandbox):
        # Only the adapter's own Thread directories bypass the protected-state
        # check. Explicit grants cannot authorize arbitrary application state.
        for root in (*canonical, *(Path(grant.path) for grant in launch.sandbox.grants)):
            root = root.resolve(strict=True)
            if root == thread_root and root in canonical:
                continue
            if any(
                root.is_relative_to(path.resolve()) or path.resolve().is_relative_to(root) for path in protected_roots
            ):
                raise _unavailable("A Sandbox grant overlaps Host configuration or application state.")
        grants.extend(launch.sandbox.grants)
        for root in canonical:
            if root == thread_root:
                for name, access in (("attachments", GrantAccess.READ_ONLY), ("tmp", GrantAccess.READ_WRITE)):
                    child = root / name
                    child.mkdir(exist_ok=True)
                    grants.append(SandboxGrant(path=child.as_posix(), access=access))
            else:
                grants.append(SandboxGrant(path=root.as_posix(), access=project_access))
        launch = launch.model_copy(update={"sandbox": RestrictedSandbox(mode="restricted", grants=tuple(grants))})
    shell = Path("/bin/bash").resolve(strict=True)
    launch = launch.model_copy(
        update={
            "default_working_directory": launch.default_working_directory or canonical[0],
            "trusted_executable_roots": launch.trusted_executable_roots or (Path("/usr/bin"), Path("/bin")),
            "shell_profiles": launch.shell_profiles
            or (LocalEnvdShellProfile(profile_id="default", executable=shell, fixed_arguments=("-c",)),),
        }
    )
    return LocalEnvdProviderRuntime(
        executable=executable,
        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=runtime_parent),
        configuration=launch,
    )


async def validate_sandbox_runtime(
    executable: Path,
    project_path: Path,
    *,
    protected_roots: tuple[Path, ...] = (),
    owned_probe_root: Path | None = None,
) -> None:
    """Probe actual Device, file and process operations inside the production boundary."""
    # This private parent is deliberately outside the sole granted project root.
    with tempfile.TemporaryDirectory(prefix="a13n-sandbox-probe-") as temporary:
        parent = Path(temporary).resolve()
        sentinel = parent / "host-only"
        await to_thread.run_sync(sentinel.write_text, "not granted")
        runtime = await to_thread.run_sync(
            lambda: create_sandbox_runtime(
                executable, roots=(project_path,), protected_roots=protected_roots, thread_files_root=owned_probe_root
            )
        )
        connector = LOCAL_ENVD.execution_connector(
            LocalEnvdEnvironmentConfiguration(working_directory=project_path.resolve().as_posix()),
            runtime=runtime,
            environment_id="sandbox-probe",
        )
        environment = None
        server = await asyncio.start_server(lambda reader, writer: writer.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            environment = await connector.open()
            files, processes = environment.operations.files, environment.operations.processes
            assert files is not None and processes is not None
            from a13n_environment.models import EnvironmentError

            try:
                await files.read_text(sentinel.as_posix())
            except EnvironmentError:
                pass
            else:
                raise _unavailable("Sandbox exposed an unrelated Host file.")
            process_check = f"test ! -e /proc/{os.getpid()}/status || exit 42; " if sys.platform == "linux" else ""
            script = (
                f"test ! -r {shlex.quote(str(sentinel))} || exit 40; "
                f"if (echo probe > /dev/tcp/127.0.0.1/{port}) 2>/dev/null; then exit 41; fi; "
                f"{process_check}printf sandbox-ready"
            )
            started = await processes.start(
                CommandRequest(
                    command=ShellCommand(profile_id="default", script=script),
                    output_policy=EnvironmentOutputPolicy(
                        max_inline_bytes=1024, max_output_bytes=4096, overflow="retain"
                    ),
                )
            )
            result = await processes.wait(started.process.handle, condition="tree_cleaned", timeout_seconds=10)
            if result.status.exit_code != 0:
                raise _unavailable("Sandbox filesystem, process or denied-network probe failed.")
            await processes.release(started.process.handle)
        finally:
            with CancelScope(shield=True):
                server.close()
                await server.wait_closed()
                try:
                    if environment is not None:
                        await environment.close()
                finally:
                    await runtime.close()


def _unavailable(message: str) -> EnvironmentLifecycleError:
    return EnvironmentLifecycleError(message, code="sandbox_unavailable")
