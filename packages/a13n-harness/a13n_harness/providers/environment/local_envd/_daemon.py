from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import stat
import subprocess
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from a13n_envd_client import EIPDeviceConnection, StdioTransport
from a13n_envd_client import __version__ as envd_client_version
from anyio import CancelScope

from ..errors import EnvironmentProviderError, provider_error
from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import EnvironmentProviderOutcomeCertainty as Certainty
from ..errors import EnvironmentProviderRecoveryHint as Recovery
from .configuration import LocalEnvdLaunchConfiguration

if TYPE_CHECKING:
    from .runtime import LocalEnvdRuntimeAllocator

_PROVIDER_KEY = "local_envd"
_PROCESS_POLL_SECONDS = 0.01
_SUBPROCESS_TIMEOUT_SECONDS = 30.0
_TERMINATE_GRACE_SECONDS = 5.0
_PYTHON_RELEASE_VERSION = re.compile(r"^(?P<base>[0-9]+\.[0-9]+\.[0-9]+)(?:rc(?P<rc>[1-9][0-9]*))?$")


@dataclass(frozen=True, slots=True)
class LocalEnvdProcessLaunch:
    """Host-owned native launch envelope; never serialized as Provider configuration."""

    arguments: tuple[str, ...]
    environment: Mapping[str, str]


type LocalEnvdLaunchFactory = Callable[[Path, Path, Path, Mapping[str, str]], LocalEnvdProcessLaunch]


class LocalDaemon:
    """Private launch owner. Session adapters never receive native process ownership."""

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self._close_task: asyncio.Task[None] | None = None
        self.device: EIPDeviceConnection | None = None
        self.configuration: LocalEnvdLaunchConfiguration | None = None

    async def launch(
        self,
        executable: Path,
        allocate: LocalEnvdRuntimeAllocator,
        configuration: LocalEnvdLaunchConfiguration,
        *,
        device_id: str,
        launch_factory: LocalEnvdLaunchFactory | None = None,
    ) -> EIPDeviceConnection:
        try:
            await validate_local_envd_runtime(executable)
            configuration = await asyncio.to_thread(_canonical_configuration, configuration)
            self.configuration = configuration
            root = await self._stack.enter_async_context(allocate())
            root = await asyncio.to_thread(_prepare_allocation_root, root)
            runtime_dir, config_path, stderr_path = await asyncio.to_thread(
                _write_private_bootstrap,
                root,
                configuration,
            )
            stderr = self._stack.enter_context(open(stderr_path, "wb"))
            options: dict[str, Any] = {}
            job = None
            if os.name == "posix":
                options["start_new_session"] = True
            elif os.name == "nt":  # pragma: no cover - Windows CI
                from .._windows_job import WindowsJob

                job = WindowsJob.create()
                self._stack.callback(job.close)
                self._stack.push_async_callback(
                    asyncio.to_thread,
                    job.terminate_and_wait,
                    timeout_seconds=_TERMINATE_GRACE_SECONDS,
                    poll_seconds=_PROCESS_POLL_SECONDS,
                )
                options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | job.creation_flags
            environment = _daemon_environment(device_id=device_id, runtime_dir=runtime_dir)
            launch = (
                LocalEnvdProcessLaunch((str(executable), "--config", str(config_path)), environment)
                if launch_factory is None
                else await asyncio.to_thread(launch_factory, executable, config_path, runtime_dir, environment)
            )
            process = await asyncio.create_subprocess_exec(
                *launch.arguments,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=stderr,
                env=launch.environment,
                **options,
            )
            self._stack.push_async_callback(_terminate_process_tree, process)
            if job is not None:
                job.assign_and_resume(process.pid)
            self.device = await EIPDeviceConnection.initialize(
                StdioTransport.from_process(process),
                expected_device_id=device_id,
                initialization_timeout=10,
                request_timeout=30,
            )
            self._stack.push_async_callback(self.device.close)
            return self.device
        except BaseException as error:
            try:
                await self.close()
            except BaseException as cleanup_error:
                error.add_note(f"Local daemon launch cleanup also failed: {cleanup_error!r}")
            raise

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._stack.aclose(), name="local-envd-daemon-close")
        # Host-owned cleanup must finish before the runtime releases its launch lock.
        cancellation: asyncio.CancelledError | None = None
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError as error:
                cancellation = error
        self._close_task.result()
        if cancellation is not None:
            raise cancellation


def _canonical_configuration(configuration: LocalEnvdLaunchConfiguration) -> LocalEnvdLaunchConfiguration:
    directory = configuration.default_working_directory
    if directory is None:
        directory = Path.cwd()
    directory = _canonical_directory(directory, "Local Envd default working directory")
    roots = tuple(
        sorted({_canonical_directory(path, "executable root") for path in configuration.trusted_executable_roots})
    )
    profiles = tuple(
        profile.model_copy(
            update={
                "executable": _canonical_file(profile.executable, "shell executable", executable=True),
            }
        )
        for profile in configuration.shell_profiles
    )
    return configuration.model_copy(
        update={
            "default_working_directory": directory,
            "trusted_executable_roots": roots,
            "shell_profiles": profiles,
        }
    )


async def validate_local_envd_runtime(executable: Path) -> None:
    """Validate the exact native release, without claiming an envd security boundary."""
    await asyncio.to_thread(_validate_runtime_executable, executable)
    environment = {name: value for name, value in os.environ.items() if not name.startswith("A13N_ENVD_")}
    stdout, stderr = await _run_checked_subprocess(executable, "--version", environment=environment)
    if stdout != f"a13n-envd {_client_release_identity()}\n".encode() or stderr:
        raise _runtime_failure(f"Local Envd release does not match a13n-envd-client {envd_client_version}.")


async def _stop_validation_process(process: asyncio.subprocess.Process) -> None:
    async def drain(stream: asyncio.StreamReader | None) -> None:
        if stream is not None:
            while await stream.read(8192):
                pass

    with CancelScope(shield=True):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            elif process.returncode is None:
                process.kill()
        except ProcessLookupError:
            pass
        async with asyncio.timeout(_TERMINATE_GRACE_SECONDS):
            await asyncio.gather(drain(process.stdout), drain(process.stderr), process.wait())


def _write_private_bootstrap(root: Path, configuration: LocalEnvdLaunchConfiguration) -> tuple[Path, Path, Path]:
    runtime_dir = root / "envd-runtime"
    runtime_dir.mkdir(mode=0o700)
    config_path = root / "a13n-envd.json"
    profiles = [
        {
            "profile_id": profile.profile_id,
            "display_name": profile.profile_id,
            "native_executable": str(profile.executable),
            "fixed_arguments": list(profile.fixed_arguments),
            "safe_base_environment": {},
            "executable_search_roots": [
                str(path)
                for path in dict.fromkeys((*configuration.trusted_executable_roots, profile.executable.parent))
            ],
            "max_script_bytes": profile.max_script_bytes,
            "allow_login_mode": profile.allow_login,
        }
        for profile in configuration.shell_profiles
    ]
    payload = {
        "default_working_directory": str(configuration.default_working_directory),
        "directory_discovery": configuration.directory_discovery,
        "limits": {
            name: getattr_limit
            for name, getattr_limit in configuration.model_dump(mode="json").items()
            if name.startswith("max_")
        },
        "trusted_executable_roots": [str(path) for path in configuration.trusted_executable_roots],
        "shell_profiles": profiles,
    }
    descriptor = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(payload, stream, separators=(",", ":"), sort_keys=True)
    return runtime_dir, config_path, root / "a13n-envd.stderr.log"


def _daemon_environment(*, device_id: str, runtime_dir: Path) -> dict[str, str]:
    environment = {name: value for name, value in os.environ.items() if not name.startswith("A13N_ENVD_")}
    environment.update(
        {
            "A13N_ENVD_DEVICE_ID": device_id,
            "A13N_ENVD_RUNTIME_DIR": str(runtime_dir),
            "A13N_ENVD_TRANSPORT": "stdio",
        }
    )
    return environment


def _canonical_directory(path: Path, label: str) -> Path:
    try:
        canonical = path.resolve(strict=True)
        if not canonical.is_dir():
            raise _spec_error(f"{label} is not a directory: {canonical}")
        return canonical
    except (FileNotFoundError, NotADirectoryError) as error:
        raise provider_error(
            _PROVIDER_KEY,
            "provider_resource_missing",
            Category.MISSING,
            certainty=Certainty.KNOWN,
            recovery_hint=Recovery.FIX_INPUT,
            description=f"{label} does not exist: {path}",
        ) from error
    except PermissionError as error:
        raise provider_error(
            _PROVIDER_KEY,
            "provider_denied",
            Category.DENIED,
            certainty=Certainty.KNOWN,
            recovery_hint=Recovery.NONE,
            description=f"{label} is not accessible: {path}",
        ) from error
    except EnvironmentProviderError:
        raise
    except (OSError, ValueError) as error:
        raise _runtime_failure(f"{label} could not be inspected: {path}") from error


def _canonical_file(path: Path, label: str, *, executable: bool) -> Path:
    try:
        canonical = path.resolve(strict=True)
        metadata = canonical.stat()
    except (FileNotFoundError, NotADirectoryError) as error:
        raise provider_error(
            _PROVIDER_KEY,
            "provider_resource_missing",
            Category.MISSING,
            certainty=Certainty.KNOWN,
            recovery_hint=Recovery.FIX_INPUT,
            description=f"{label} does not exist: {path}",
        ) from error
    except PermissionError as error:
        raise provider_error(
            _PROVIDER_KEY,
            "provider_denied",
            Category.DENIED,
            certainty=Certainty.KNOWN,
            recovery_hint=Recovery.NONE,
            description=f"{label} is not accessible: {path}",
        ) from error
    except (OSError, ValueError) as error:
        raise _runtime_failure(f"{label} could not be inspected: {path}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise _spec_error(f"{label} is not a regular file: {canonical}")
    if executable and not os.access(canonical, os.X_OK):
        raise _spec_error(f"{label} is not executable: {canonical}")
    return canonical


def _validate_runtime_executable(executable: Path) -> None:
    try:
        metadata = executable.stat()
    except (FileNotFoundError, NotADirectoryError) as error:
        raise _runtime_failure(f"Local Envd a13n-envd executable does not exist: {executable}") from error
    except PermissionError as error:
        raise _runtime_failure(f"Local Envd a13n-envd executable is not accessible: {executable}") from error
    except (OSError, ValueError) as error:
        raise _runtime_failure(f"Local Envd a13n-envd executable cannot be inspected: {executable}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise _runtime_failure(f"Local Envd a13n-envd executable is not a regular file: {executable}")
    if not os.access(executable, os.X_OK):
        raise _runtime_failure(f"Local Envd a13n-envd executable is not executable: {executable}")


async def _run_checked_subprocess(
    executable: Path,
    *arguments: str,
    environment: dict[str, str] | None,
) -> tuple[bytes, bytes]:
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            str(executable),
            *arguments,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
            start_new_session=os.name == "posix",
        )
        async with asyncio.timeout(_SUBPROCESS_TIMEOUT_SECONDS):
            stdout, stderr = await _bounded_validation_output(process)
    except asyncio.CancelledError:
        if process is not None:
            await _stop_validation_process(process)
        raise
    except TimeoutError as error:
        if process is not None:
            await _stop_validation_process(process)
        raise provider_error(
            _PROVIDER_KEY,
            "provider_timeout",
            Category.TIMEOUT,
            description="Local Envd runtime validation timed out.",
        ) from error
    except EnvironmentProviderError:
        if process is not None:
            await _stop_validation_process(process)
        raise
    except (OSError, subprocess.SubprocessError) as error:
        if process is not None:
            await _stop_validation_process(process)
        raise _runtime_failure("Local Envd runtime validation could not execute a13n-envd.") from error
    assert process is not None
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[:512]
        raise _runtime_failure(f"Local Envd runtime validation failed with code {process.returncode}: {detail}")
    return stdout, stderr


async def _bounded_validation_output(process: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
    async def read(stream: asyncio.StreamReader | None) -> bytes:
        assert stream is not None
        chunks: list[bytes] = []
        size = 0
        while chunk := await stream.read(8192):
            size += len(chunk)
            if size > 64 * 1024:
                raise _runtime_failure("Local Envd runtime validation output exceeded its bounded limit.")
            chunks.append(chunk)
        return b"".join(chunks)

    readers = [asyncio.create_task(read(process.stdout)), asyncio.create_task(read(process.stderr))]
    try:
        stdout, stderr = await asyncio.gather(*readers)
        await process.wait()
        return stdout, stderr
    finally:
        for reader in readers:
            reader.cancel()
        with CancelScope(shield=True):
            await asyncio.gather(*readers, return_exceptions=True)


def _prepare_allocation_root(value: object) -> Path:
    if not isinstance(value, Path):
        raise _runtime_failure("Local Envd private runtime allocator must yield pathlib.Path.")
    try:
        root = value.resolve(strict=True)
        if not root.is_dir() or not root.is_absolute():
            raise _runtime_failure("Local Envd private runtime allocator must yield an existing absolute directory.")
        if next(root.iterdir(), None) is not None:
            raise _runtime_failure("Local Envd private runtime allocator must yield a fresh empty directory.")
        return root
    except EnvironmentProviderError:
        raise
    except (OSError, ValueError) as error:
        raise _runtime_failure("Local Envd private runtime allocation could not be inspected.") from error


def _client_release_identity() -> str:
    matched = _PYTHON_RELEASE_VERSION.fullmatch(envd_client_version)
    if matched is None:
        raise _runtime_failure(f"a13n-envd-client has unsupported release version {envd_client_version!r}.")
    release = matched.group("base")
    rc = matched.group("rc")
    return release if rc is None else f"{release}-rc.{rc}"


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        try:
            async with asyncio.timeout(_TERMINATE_GRACE_SECONDS):
                await process.wait()
        except TimeoutError:
            pass
    if os.name == "posix":
        await _terminate_posix_process_group(process.pid)
        await process.wait()
        return
    if process.returncode is None:  # pragma: no cover - exercised on Windows
        process.terminate()
        try:
            async with asyncio.timeout(_TERMINATE_GRACE_SECONDS):
                await process.wait()
                return
        except TimeoutError:
            process.kill()
    await process.wait()


async def _terminate_posix_process_group(process_group: int) -> None:
    if not _posix_process_group_exists(process_group):
        return
    os.killpg(process_group, signal.SIGTERM)
    try:
        async with asyncio.timeout(_TERMINATE_GRACE_SECONDS):
            while _posix_process_group_exists(process_group):
                await asyncio.sleep(_PROCESS_POLL_SECONDS)
            return
    except TimeoutError:
        pass
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        return
    async with asyncio.timeout(_TERMINATE_GRACE_SECONDS):
        while _posix_process_group_exists(process_group):
            await asyncio.sleep(_PROCESS_POLL_SECONDS)


def _posix_process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _spec_error(description: str) -> EnvironmentProviderError:
    return provider_error(_PROVIDER_KEY, "provider_spec_invalid", Category.INVALID, description=description)


def _runtime_failure(description: str) -> EnvironmentProviderError:
    return provider_error(
        _PROVIDER_KEY,
        "provider_unavailable",
        Category.UNAVAILABLE,
        certainty=Certainty.KNOWN,
        description=description,
    )
