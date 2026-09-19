"""Host-owned outer isolation for the complete local envd Device."""

from __future__ import annotations

import asyncio
import os
import shlex
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from a13n_environment import CommandRequest, EnvironmentOutputPolicy, LocalEnvdEnvironment, ShellCommand
from a13n_environment.local_envd import (
    LocalEnvdLaunchConfiguration,
    LocalEnvdProcessLaunch,
    LocalEnvdProviderConfiguration,
    LocalEnvdProviderRuntime,
    LocalEnvdShellProfile,
    TemporaryLocalEnvdRuntimeAllocator,
)
from anyio import CancelScope, to_thread

from a13n_harness_ui.errors import EnvironmentLifecycleError

_LINUX_SYSTEM = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc/ld.so.cache", "/etc/localtime")
_MACOS_SYSTEM = ("/System", "/usr/bin", "/usr/lib", "/bin", "/sbin", "/private/var/db/timezone")


@dataclass(frozen=True, slots=True)
class SandboxLaunch:
    """One immutable filesystem grant set; cwd never changes this authority."""

    roots: tuple[Path, ...]

    def __call__(
        self, executable: Path, config: Path, runtime: Path, environment: Mapping[str, str]
    ) -> LocalEnvdProcessLaunch:
        home = runtime.parent / "home"
        temporary = runtime.parent / "tmp"
        home.mkdir(mode=0o700)
        temporary.mkdir(mode=0o700)
        clean = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(home),
            "TMPDIR": str(temporary),
            "LANG": "C.UTF-8",
            **{
                key: environment[key] for key in ("A13N_ENVD_DEVICE_ID", "A13N_ENVD_RUNTIME_DIR", "A13N_ENVD_TRANSPORT")
            },
        }
        writable = (*self.roots, runtime, home, temporary)
        if sys.platform == "linux":
            launcher = Path("/usr/bin/bwrap")
            if not launcher.is_file():
                raise _unavailable("Sandbox requires /usr/bin/bwrap.")
            arguments = [
                str(launcher),
                "--unshare-user",
                "--unshare-pid",
                "--unshare-ipc",
                "--unshare-uts",
                "--unshare-net",
                "--disable-userns",
                "--assert-userns-disabled",
                "--new-session",
                "--die-with-parent",
                "--as-pid-1",
                "--cap-drop",
                "ALL",
                "--clearenv",
            ]
            for raw in _LINUX_SYSTEM:
                path = Path(raw)
                if path.is_symlink():
                    arguments.extend(("--symlink", os.readlink(path), raw))
                elif path.exists():
                    arguments.extend(("--ro-bind", raw, raw))
            arguments.extend(("--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"))
            for path in sorted(set(writable), key=lambda item: len(item.parts)):
                arguments.extend(("--bind", str(path), str(path)))
            for path in (executable.resolve(), config):
                arguments.extend(("--ro-bind", str(path), str(path)))
            for key, value in clean.items():
                arguments.extend(("--setenv", key, value))
            arguments.extend(("--chdir", str(self.roots[0]), "--", str(executable.resolve()), "--config", str(config)))
            return LocalEnvdProcessLaunch(tuple(arguments), clean)
        if sys.platform == "darwin":
            launcher = Path("/usr/bin/sandbox-exec")
            if not launcher.is_file():
                raise _unavailable("Sandbox requires /usr/bin/sandbox-exec.")
            profile = [
                "(version 1)",
                "(deny default)",
                "(allow process-exec process-fork)",
                "(allow signal (target same-sandbox))",
                "(allow process-info* (target same-sandbox))",
                "(allow sysctl-read)",
                '(allow file-read* file-write* (literal "/dev/null") (literal "/dev/zero") (literal "/dev/random") (literal "/dev/urandom"))',
            ]
            arguments = [str(launcher)]
            for index, path in enumerate((*map(Path, _MACOS_SYSTEM), executable.resolve(), config)):
                name = f"READ_{index}"
                arguments.extend(("-D", f"{name}={path}"))
                kind = "subpath" if path.is_dir() else "literal"
                profile.append(f'(allow file-read* ({kind} (param "{name}")))')
            for index, path in enumerate(writable):
                name = f"WRITE_{index}"
                arguments.extend(("-D", f"{name}={path}"))
                profile.append(f'(allow file-read* file-write* (subpath (param "{name}")))')
            arguments.extend(("-p", "\n".join(profile), str(executable.resolve()), "--config", str(config)))
            return LocalEnvdProcessLaunch(tuple(arguments), clean)
        raise _unavailable("Local Sandbox is not available on this platform.")


def create_sandbox_runtime(
    executable: Path,
    *,
    roots: tuple[Path, ...],
    runtime_parent: Path | None = None,
    protected_roots: tuple[Path, ...] = (),
    thread_files_root: Path | None = None,
) -> LocalEnvdProviderRuntime:
    """Build an inert Device owner; broad roots cannot expose Host private state."""
    if sys.platform not in {"linux", "darwin"}:
        raise _unavailable("Local Sandbox is not available on this platform.")
    canonical = tuple(dict.fromkeys(root.resolve(strict=True) for root in roots))
    if not canonical or any(not root.is_dir() for root in canonical):
        raise _unavailable("Sandbox roots must be existing directories.")
    for root in canonical:
        if thread_files_root is not None and root == thread_files_root.resolve():
            continue
        if any(root.is_relative_to(path.resolve()) or path.resolve().is_relative_to(root) for path in protected_roots):
            raise _unavailable("A Sandbox Project root overlaps Host configuration or application state.")
    shell = Path("/bin/bash").resolve(strict=True)
    return LocalEnvdProviderRuntime(
        executable=executable,
        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=runtime_parent),
        configuration=LocalEnvdLaunchConfiguration(
            default_working_directory=canonical[0],
            trusted_executable_roots=(Path("/usr/bin"), Path("/bin")),
            shell_profiles=(LocalEnvdShellProfile(profile_id="default", executable=shell, fixed_arguments=("-c",)),),
        ),
        launch_factory=SandboxLaunch(canonical),
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
        environment = LocalEnvdEnvironment(
            LocalEnvdProviderConfiguration(working_directory=project_path.resolve().as_posix()),
            runtime,
            environment_id="sandbox-probe",
        )
        server = await asyncio.start_server(lambda reader, writer: writer.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            await environment.prepare()
            files, processes = environment.operations.files, environment.operations.processes
            assert files is not None and processes is not None
            from a13n_environment import EnvironmentError

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
                    await environment.close()
                finally:
                    await runtime.close()


def _unavailable(message: str) -> EnvironmentLifecycleError:
    return EnvironmentLifecycleError(message, code="sandbox_unavailable")
