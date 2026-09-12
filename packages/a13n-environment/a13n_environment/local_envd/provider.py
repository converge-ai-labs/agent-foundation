from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import stat
import subprocess
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from a13n_envd_client import __version__ as envd_client_version
from anyio import CancelScope
from pydantic import BaseModel

from .._local_identity import local_backing_identity
from ..attachments import StdioEIPCarrier
from ..eip import EIPEnvironmentSession, open_eip_environment
from ..eip.binding import configured_descriptor
from ..errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
)
from ..management import Environment, EnvironmentProvider, HostLocalProviderConfiguration, ProviderRuntimeContext
from ..models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentOperationFamily,
    EnvironmentState,
)
from ..operations import EnvironmentOperations
from .configuration import LocalEnvdProviderConfiguration
from .runtime import LocalEnvdProviderRuntime

if TYPE_CHECKING:
    from .._windows_job import WindowsJob

_PROVIDER_KEY = "a13n.local-envd"
_CONFIGURATION_VERSION = "1"
_STARTUP_TIMEOUT_SECONDS = 10.0
_PROCESS_POLL_SECONDS = 0.01
_SUBPROCESS_TIMEOUT_SECONDS = 30.0
_TERMINATE_GRACE_SECONDS = 5.0
_DAEMON_MAX_REQUEST_BYTES = 16 * 1024 * 1024
_DAEMON_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_DAEMON_MAX_TRANSFER_FRAME_BYTES = 4 * 1024 * 1024
_READ_OPERATIONS = ("stat", "read_text", "open_reader", "list", "find", "search")
_WRITE_OPERATIONS = ("write_text", "open_writer", "mkdir", "patch_text", "copy", "remove", "move")
_PYTHON_RELEASE_VERSION = re.compile(r"^(?P<base>[0-9]+\.[0-9]+\.[0-9]+)(?:rc(?P<rc>[1-9][0-9]*))?$")


class LocalEnvdEnvironmentProvider(EnvironmentProvider):
    """Inert singleton-style Provider for fresh private Local Envd generations."""

    provider_configuration_model = HostLocalProviderConfiguration

    @property
    def display_name(self) -> str:
        return "Local Envd"

    @property
    def key(self) -> str:
        return _PROVIDER_KEY

    @property
    def configuration_models(self) -> dict[str, type[BaseModel]]:
        return {"1": LocalEnvdProviderConfiguration}

    async def create_runtime(
        self, *, configuration: BaseModel, credential: BaseModel | None, context: ProviderRuntimeContext
    ) -> LocalEnvdProviderRuntime:
        from .runtime import TemporaryLocalEnvdRuntimeAllocator, resolve_a13n_envd_executable

        executable = await asyncio.to_thread(resolve_a13n_envd_executable)
        return LocalEnvdProviderRuntime(
            executable=executable, allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator()
        )

    def describe_configuration(self, configuration: BaseModel) -> EnvironmentDescriptor:
        if not isinstance(configuration, LocalEnvdProviderConfiguration):
            raise TypeError("Unexpected Provider recipe")
        return configured_descriptor()

    def target_identity(self, *, configuration: BaseModel, state: EnvironmentState | None) -> str | None:
        if not isinstance(configuration, LocalEnvdProviderConfiguration) or state is not None:
            raise ValueError("Local Providers require a valid stateless workspace configuration")
        return str(configuration.workspace.path)

    def create_environment(
        self,
        *,
        configuration: BaseModel,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: object | None = None,
    ) -> Environment:
        if not isinstance(configuration, LocalEnvdProviderConfiguration):
            raise TypeError("Local Envd requires LocalEnvdProviderConfiguration")
        if state is not None:
            raise _provider_error(
                "Local Envd is stateless and does not accept Environment state.",
                code="provider_state_invalid",
                category=EnvironmentProviderErrorCategory.INVALID,
            )
        if not isinstance(runtime, LocalEnvdProviderRuntime):
            raise TypeError("Local Envd requires LocalEnvdProviderRuntime")
        return LocalEnvdEnvironment(configuration, runtime, environment_id=environment_id)


class LocalEnvdEnvironment(Environment):
    def __init__(
        self,
        configuration: LocalEnvdProviderConfiguration,
        runtime: LocalEnvdProviderRuntime,
        *,
        environment_id: str,
    ) -> None:
        super().__init__(None)
        self._environment_id = environment_id
        self._configuration = configuration.model_copy(deep=True)
        self._runtime = runtime
        self._descriptor: EnvironmentDescriptor = configured_descriptor()
        self._availability = EnvironmentAvailability(status="preparing")
        self._operations = EnvironmentOperations()
        self._allocation: AbstractAsyncContextManager[Path] | None = None
        self._allocation_entered = False
        self._process: asyncio.subprocess.Process | None = None
        self._windows_job: WindowsJob | None = None
        self._carrier: StdioEIPCarrier | None = None
        self._stderr_file: Any | None = None
        self._eip_scope: AbstractAsyncContextManager[EIPEnvironmentSession] | None = None
        self._bound_eip: EIPEnvironmentSession | None = None
        self._cleanup_task: asyncio.Task[BaseException | None] | None = None

    @property
    def provider_key(self) -> str:
        return _PROVIDER_KEY

    @property
    def environment_id(self) -> str:
        return self._environment_id

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        if self._descriptor is None:
            raise RuntimeError("Environment descriptor is unavailable before entry")
        return self._descriptor

    @property
    def availability(self) -> EnvironmentAvailability:
        return self._availability

    @property
    def operations(self) -> EnvironmentOperations:
        return self._operations

    async def _prepare(
        self,
        *,
        thread_id: str,
        run_id: str,
        agent_instance_id: str,
        mount_id: str,
        host_refs: Mapping[str, str],
    ) -> None:
        del thread_id, run_id, agent_instance_id, host_refs
        try:
            configuration = await asyncio.to_thread(_canonical_configuration, self._configuration)
            await _validate_runtime(self._runtime.executable, configuration)
            self._configuration = configuration
            backing_identity = await asyncio.to_thread(
                local_backing_identity,
                provider_key=_PROVIDER_KEY,
                roots=(configuration.workspace.path, *configuration.trusted_executable_roots),
                policy=configuration.model_dump(mode="json"),
            )
            await self._launch_private_generation()

            carrier = self._carrier
            if carrier is None:
                raise _runtime_failure("Local Envd did not create an EIP carrier.")
            source = carrier.lease(
                initialization_timeout=_STARTUP_TIMEOUT_SECONDS,
                request_timeout=_STARTUP_TIMEOUT_SECONDS,
            )
            scope = open_eip_environment(
                provider_key=_PROVIDER_KEY,
                environment_id=self.environment_id,
                session_source=source,
                mount_id=mount_id,
            )
            self._eip_scope = scope
            bound = await scope.__aenter__()
            self._bound_eip = bound
            await bound.ensure_ready(bound.descriptor.operation_families)
            facets = bound.operations
            self._descriptor = bound.descriptor.model_copy(update={"backing_identity": backing_identity})
            self._operations = EnvironmentOperations(
                files=facets.files,
                shell=facets.shell,
                processes=facets.processes,
                ports=facets.ports,
                outputs=facets.outputs,
            )
            self._availability = bound.availability
        except BaseException as entry_error:
            cleanup_error = await self._cleanup_local_runtime()
            if isinstance(entry_error, asyncio.CancelledError):
                if cleanup_error is not None:
                    entry_error.add_note(f"Local Envd entry cleanup also failed: {cleanup_error!r}")
                raise
            primary_error = _normalize_entry_error(entry_error)
            if cleanup_error is not None:
                raise BaseExceptionGroup(
                    "Local Envd entry and cleanup failed",
                    [primary_error, cleanup_error],
                ) from None
            if primary_error is entry_error:
                raise
            raise primary_error from entry_error

    async def _launch_private_generation(self) -> None:
        allocation = self._runtime.allocate_private_runtime()
        if not isinstance(allocation, AbstractAsyncContextManager):
            raise _runtime_failure("Local Envd private runtime allocator returned an invalid context manager.")
        self._allocation = allocation
        root = await allocation.__aenter__()
        self._allocation_entered = True
        allocation_root = await asyncio.to_thread(_prepare_allocation_root, root)
        runtime_dir, config_path, stderr_path = await asyncio.to_thread(
            _write_private_bootstrap,
            allocation_root,
            self._configuration,
        )
        self._stderr_file = await asyncio.to_thread(open, stderr_path, "wb")
        subprocess_options: dict[str, Any] = {}
        if os.name == "posix":
            subprocess_options["start_new_session"] = True
        elif os.name == "nt":  # pragma: no cover - exercised on Windows
            from .._windows_job import WindowsJob

            self._windows_job = WindowsJob.create()
            subprocess_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | self._windows_job.creation_flags
        self._process = await asyncio.create_subprocess_exec(
            str(self._runtime.executable),
            "--config",
            str(config_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self._stderr_file,
            env=_daemon_environment(environment_id=self.environment_id, runtime_dir=runtime_dir),
            **subprocess_options,
        )
        if self._windows_job is not None:
            self._windows_job.assign_and_resume(self._process.pid)
        self._carrier = StdioEIPCarrier(
            self._process,
            max_request_bytes=_DAEMON_MAX_REQUEST_BYTES,
            max_response_bytes=_DAEMON_MAX_RESPONSE_BYTES,
            max_transfer_frame_bytes=_DAEMON_MAX_TRANSFER_FRAME_BYTES,
        )

    def _bind_mount(self, mount_id: str) -> None:
        if self._bound_eip is not None:
            self._bound_eip.bind_mount(mount_id)
            self._operations = self._bound_eip.operations

    async def _ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        bound = self._bound_eip
        if bound is None:
            raise _operation_error("Local Envd EIP session is unavailable.", "environment_unavailable")
        await bound.ensure_ready(operations)
        self._availability = bound.availability

    async def _close(self) -> None:
        self._availability = EnvironmentAvailability(status="unavailable")
        self._operations = EnvironmentOperations()
        cleanup_error = await self._cleanup_local_runtime()
        if cleanup_error is not None:
            raise cleanup_error

    async def reconcile(self) -> Literal["running", "stopped", "absent"]:
        # These adapters own no durable daemon; their process-local resources
        # end with their owner. Workspace paths are externally retained.
        return "stopped"

    async def _destroy(self) -> None:
        return None

    async def _cleanup_local_runtime(self) -> BaseException | None:
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._perform_cleanup(), name="local-envd-environment-cleanup")
        return await _await_cleanup(self._cleanup_task)

    async def _perform_cleanup(self) -> BaseException | None:
        errors: list[BaseException] = []
        if self._eip_scope is not None:
            try:
                await self._eip_scope.__aexit__(None, None, None)
            except BaseException as error:
                errors.append(error)
            self._eip_scope = None
            self._bound_eip = None
        carrier, process, windows_job = self._carrier, self._process, self._windows_job
        if carrier is not None:
            try:
                await carrier.close()
            except BaseException as error:
                errors.append(error)
        if process is not None:
            try:
                await _terminate_process_tree(process)
            except BaseException as error:
                errors.append(error)
        if windows_job is not None:
            try:
                await asyncio.to_thread(
                    windows_job.terminate_and_wait,
                    timeout_seconds=_TERMINATE_GRACE_SECONDS,
                    poll_seconds=_PROCESS_POLL_SECONDS,
                )
            except BaseException as error:
                errors.append(error)
            try:
                await asyncio.to_thread(windows_job.close)
            except BaseException as error:
                errors.append(error)
            self._windows_job = None
        if self._stderr_file is not None:
            try:
                await asyncio.to_thread(self._stderr_file.close)
            except BaseException as error:
                errors.append(error)
            self._stderr_file = None
        if self._allocation is not None and self._allocation_entered:
            try:
                await self._allocation.__aexit__(None, None, None)
            except BaseException as error:
                errors.append(error)
            self._allocation_entered = False
        self._allocation = None
        if not errors:
            return None
        wrapped = EnvironmentProviderError(
            "Local Envd could not prove complete EIP/process/private-runtime cleanup.",
            code="provider_cleanup_failed",
            category=EnvironmentProviderErrorCategory.CLEANUP,
            certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
            context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
            details={"failure_count": len(errors)},
        )
        for error in errors:
            wrapped.add_note(repr(error))
        return wrapped


def _canonical_configuration(configuration: LocalEnvdProviderConfiguration) -> LocalEnvdProviderConfiguration:
    workspace = _canonical_directory(configuration.workspace.path, "Local Envd workspace")
    roots = tuple(
        sorted(
            _canonical_directory(path, "Local Envd trusted executable root")
            for path in configuration.trusted_executable_roots
        )
    )
    if len(roots) != len(set(roots)):
        raise _spec_error("Local Envd trusted executable roots resolve to duplicate directories.")
    profiles = []
    for profile in configuration.shell_profiles:
        executable = _canonical_file(profile.executable, "Local Envd shell executable", executable=True)
        profiles.append(profile.model_copy(update={"executable": executable}))
    profiles.sort(key=lambda profile: profile.profile_id)
    return configuration.model_copy(
        update={
            "workspace": configuration.workspace.model_copy(update={"path": workspace}),
            "trusted_executable_roots": roots,
            "shell_profiles": tuple(profiles),
        },
        deep=True,
    )


def _canonical_directory(path: Path, label: str) -> Path:
    try:
        canonical = path.resolve(strict=True)
        if not canonical.is_dir():
            raise _spec_error(f"{label} is not a directory: {canonical}")
        return canonical
    except (FileNotFoundError, NotADirectoryError) as error:
        raise EnvironmentProviderError(
            f"{label} does not exist: {path}",
            code="provider_resource_missing",
            category=EnvironmentProviderErrorCategory.MISSING,
            certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
            recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
            context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
        ) from error
    except PermissionError as error:
        raise EnvironmentProviderError(
            f"{label} is not accessible: {path}",
            code="provider_denied",
            category=EnvironmentProviderErrorCategory.DENIED,
            certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
            context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
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
        raise EnvironmentProviderError(
            f"{label} does not exist: {path}",
            code="provider_resource_missing",
            category=EnvironmentProviderErrorCategory.MISSING,
            certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
            recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
            context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
        ) from error
    except PermissionError as error:
        raise EnvironmentProviderError(
            f"{label} is not accessible: {path}",
            code="provider_denied",
            category=EnvironmentProviderErrorCategory.DENIED,
            certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
            context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
        ) from error
    except (OSError, ValueError) as error:
        raise _runtime_failure(f"{label} could not be inspected: {path}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise _spec_error(f"{label} is not a regular file: {canonical}")
    if executable and not os.access(canonical, os.X_OK):
        raise _spec_error(f"{label} is not executable: {canonical}")
    return canonical


async def validate_local_envd_runtime(executable: Path, configuration: LocalEnvdProviderConfiguration) -> None:
    """Check exact release and production isolation without starting an EIP daemon.

    This is the same check used by prepare(); success is an observation, not a
    lease or a replacement for validation when an environment is prepared.
    """
    await _validate_runtime(executable, configuration)


async def _validate_runtime(executable: Path, configuration: LocalEnvdProviderConfiguration) -> None:
    await asyncio.to_thread(_validate_runtime_executable, executable)
    expected_version = f"a13n-envd {_client_release_identity()}\n".encode()
    stdout, stderr = await _run_checked_subprocess(executable, "--version", environment=None)
    if stdout != expected_version or stderr:
        raise _runtime_failure(f"Local Envd a13n-envd release does not match a13n-envd-client {envd_client_version}.")
    probe_environment = {name: value for name, value in os.environ.items() if not name.startswith("A13N_ENVD_")}
    probe_environment["A13N_ENVD_EXECUTION_ISOLATION"] = "required"
    probe_environment["A13N_ENVD_EXECUTION_NETWORK"] = configuration.execution_network.value
    stdout, stderr = await _run_checked_subprocess(
        executable,
        "isolation",
        "probe",
        "--json",
        environment=probe_environment,
    )
    if stderr:
        raise _runtime_failure("Local Envd isolation probe wrote unexpected stderr output.")
    try:
        report = json.loads(stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _runtime_failure("Local Envd isolation probe returned invalid JSON.") from error
    if not isinstance(report, dict) or not all(
        report.get(field) is True for field in ("ready", "isolation", "filesystem_containment", "process_containment")
    ):
        raise _runtime_failure("Local Envd required isolation probe did not report production readiness.")
    if configuration.execution_network.value == "deny" and report.get("network_isolation") is not True:
        raise _runtime_failure("Local Envd isolation probe did not enforce denied networking.")


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
        raise EnvironmentProviderError(
            "Local Envd runtime validation timed out.",
            code="provider_timeout",
            category=EnvironmentProviderErrorCategory.TIMEOUT,
            certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
            recovery_hint=EnvironmentProviderRecoveryHint.REFRESH_RUNTIME,
            context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
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


def _kill_validation_session(session_id: int) -> None:
    # Envd's production probe creates a second process group (not a second
    # session). Freeze its launcher before finding those owned groups. This is
    # cleanup for a trusted runtime, not a containment claim for hostile code.
    try:
        os.killpg(session_id, signal.SIGSTOP)
    except (ProcessLookupError, PermissionError):
        pass
    groups = {session_id}
    members: set[int] = set()
    try:
        if Path("/proc/self/stat").exists():
            pids = [int(path.name) for path in Path("/proc").iterdir() if path.name.isdigit()]
        else:  # macOS has no procfs; /bin/ps is part of the supported Host.
            listed = subprocess.run(["/bin/ps", "-axo", "pid="], capture_output=True, timeout=2, check=True)
            pids = [int(value) for value in listed.stdout.split() if value.isdigit()]
        for pid in pids:
            try:
                if os.getsid(pid) == session_id:
                    os.kill(pid, signal.SIGSTOP)
                    members.add(pid)
                    groups.add(os.getpgid(pid))
            except (ProcessLookupError, PermissionError):
                pass
    finally:
        for group in groups:
            try:
                os.killpg(group, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        for pid in members:
            try:
                if os.getsid(pid) == session_id:
                    os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


async def _stop_validation_process(process: asyncio.subprocess.Process) -> None:
    async def drain(stream: asyncio.StreamReader | None) -> None:
        if stream is not None:
            while await stream.read(8192):
                pass  # Discard; never retain cleanup output.

    # Shield only bounded cleanup. Drain paused pipe transports after killing;
    # wait() alone can hang on their buffered output even after root exit.
    with CancelScope(shield=True):
        try:
            if os.name == "posix":
                await asyncio.to_thread(_kill_validation_session, process.pid)
            elif process.returncode is None:
                process.kill()
        finally:
            async with asyncio.timeout(_TERMINATE_GRACE_SECONDS):
                await asyncio.gather(drain(process.stdout), drain(process.stderr), process.wait())


def _write_private_bootstrap(
    allocation_root: Path,
    configuration: LocalEnvdProviderConfiguration,
) -> tuple[Path, Path, Path]:
    runtime_dir = allocation_root / "envd-runtime"
    runtime_dir.mkdir(mode=0o700)
    config_path = allocation_root / "a13n-envd.json"
    stderr_path = allocation_root / "a13n-envd.stderr.log"
    command_enabled = bool(configuration.trusted_executable_roots or configuration.shell_profiles)
    allowed_operations: list[str] = list(_READ_OPERATIONS)
    if not configuration.workspace.read_only:
        allowed_operations.extend(_WRITE_OPERATIONS)
    if command_enabled:
        allowed_operations.extend(("command_cwd", "executable_source"))
    shell_profiles = []
    for profile in configuration.shell_profiles:
        search_roots = tuple(dict.fromkeys((*configuration.trusted_executable_roots, profile.executable.parent)))
        shell_profiles.append(
            {
                "profile_id": profile.profile_id,
                "display_name": profile.profile_id,
                "native_executable": str(profile.executable),
                "fixed_arguments": list(profile.fixed_arguments),
                "safe_base_environment": {},
                "executable_search_roots": [str(path) for path in search_roots],
                "max_script_bytes": profile.max_script_bytes,
                "allow_login_mode": profile.allow_login,
            }
        )
    payload = {
        "root_mount_id": "workspace",
        "limits": {
            "max_output_preview_bytes": configuration.max_output_preview_bytes,
            "max_output_bytes_per_stream": configuration.max_output_bytes_per_stream,
            "max_spool_bytes": configuration.max_spool_bytes,
        },
        "mounts": [
            {
                "mount_id": "workspace",
                "native_root": str(configuration.workspace.path),
                "writable": not configuration.workspace.read_only,
                "allow_command_execution": command_enabled,
                "max_file_bytes": configuration.max_file_bytes,
                "allowed_operations": allowed_operations,
            }
        ],
        "trusted_executable_roots": [str(path) for path in configuration.trusted_executable_roots],
        "shell_profiles": shell_profiles,
        "execution": {
            "isolation": "required",
            "network": configuration.execution_network.value,
            "extra_read_only_paths": [],
        },
    }
    descriptor = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        stream.flush()
        os.fsync(stream.fileno())
    return runtime_dir, config_path, stderr_path


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


def _daemon_environment(*, environment_id: str, runtime_dir: Path) -> dict[str, str]:
    environment = {name: value for name, value in os.environ.items() if not name.startswith("A13N_ENVD_")}
    environment.update(
        {
            "A13N_ENVD_ENVIRONMENT_ID": environment_id,
            "A13N_ENVD_RUNTIME_DIR": str(runtime_dir),
            "A13N_ENVD_TRANSPORT": "stdio",
        }
    )
    return environment


async def _await_cleanup(cleanup_task: asyncio.Task[BaseException | None]) -> BaseException | None:
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            cleanup_error = await asyncio.shield(cleanup_task)
        except asyncio.CancelledError as error:
            if cleanup_task.cancelled():
                raise
            if cancellation is None:
                cancellation = error
            continue
        except BaseException as error:
            if cancellation is not None:
                cancellation.add_note(f"Local Envd cleanup task also failed: {error!r}")
                raise cancellation from None
            raise
        if cancellation is not None:
            if cleanup_error is not None:
                cancellation.add_note(f"Local Envd cleanup also failed: {cleanup_error!r}")
            raise cancellation from None
        return cleanup_error


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


def _normalize_entry_error(error: BaseException) -> BaseException:
    if isinstance(error, EnvironmentProviderError):
        return error
    wrapped = _runtime_failure("Local Envd entry failed before EIP operation admission.")
    wrapped.add_note(repr(error))
    return wrapped


def _provider_error(
    description: str,
    *,
    code: str,
    category: EnvironmentProviderErrorCategory,
    schema_version: str | None = None,
) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        description,
        code=code,
        category=category,
        certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
        recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
        context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY, schema_version=schema_version),
    )


def _spec_error(description: str) -> EnvironmentProviderError:
    return _provider_error(
        description,
        code="provider_spec_invalid",
        category=EnvironmentProviderErrorCategory.INVALID,
        schema_version=_CONFIGURATION_VERSION,
    )


def _runtime_failure(description: str) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        description,
        code="provider_unavailable",
        category=EnvironmentProviderErrorCategory.UNAVAILABLE,
        certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
        recovery_hint=EnvironmentProviderRecoveryHint.REFRESH_RUNTIME,
        context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
    )


def _operation_error(message: str, code: str):
    from ..models import EnvironmentError

    return EnvironmentError(message, code=code)
