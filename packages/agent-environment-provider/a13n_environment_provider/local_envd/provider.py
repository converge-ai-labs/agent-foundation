from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

from a13n_envd_client import __version__ as envd_client_version
from pydantic import BaseModel, ValidationError

from ..attachments import EIPEnvironmentAttachment, EnvironmentRuntimeAttachment, StdioEIPCarrier
from ..errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
)
from ..factories import EnvironmentProviderFactory
from ..management import EnvironmentProvider, EnvironmentProviderRuntime, EnvironmentResource
from ..models import (
    EnvironmentAttachmentConcurrency,
    EnvironmentLifecycleCapabilities,
    EnvironmentManagementAction,
    EnvironmentOperationContext,
    EnvironmentPauseMode,
    EnvironmentProviderResourceState,
    EnvironmentReconciliationPhase,
    EnvironmentReconciliationResult,
    EnvironmentResourceAllocation,
)
from .configuration import (
    LocalEnvdProviderConfiguration,
    LocalEnvdProviderStateData,
    LocalEnvdResourcePhase,
)
from .runtime import LocalEnvdProviderRuntime

_PROVIDER_KEY = "a13n.local-envd"
_STATE_VERSION = "1"
_STARTUP_TIMEOUT_SECONDS = 10.0
_STARTUP_POLL_SECONDS = 0.01
_STARTUP_STABILITY_SECONDS = 0.1
_READY_MARKER_CONTENT = b"agent-envd-ready-v1\n"
_SUBPROCESS_TIMEOUT_SECONDS = 30.0
_TERMINATE_GRACE_SECONDS = 5.0
_DAEMON_MAX_REQUEST_BYTES = 16 * 1024 * 1024
_DAEMON_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_DAEMON_MAX_TRANSFER_FRAME_BYTES = 4 * 1024 * 1024
_READ_OPERATIONS = ("stat", "read_text", "open_reader", "list", "find", "search")
_WRITE_OPERATIONS = ("write_text", "open_writer", "remove", "move")
_PYTHON_RELEASE_VERSION = re.compile(r"^(?P<base>[0-9]+\.[0-9]+\.[0-9]+)(?:rc(?P<rc>[1-9][0-9]*))?$")
_CAPABILITIES = EnvironmentLifecycleCapabilities(
    pause_modes=frozenset({EnvironmentPauseMode.FILESYSTEM}),
    resource_allocation=EnvironmentResourceAllocation.MULTIPLE_FROM_SPEC,
    attachment_concurrency=EnvironmentAttachmentConcurrency.SINGLE,
)


class LocalEnvdEnvironmentProviderFactory(EnvironmentProviderFactory):
    @classmethod
    def provider_key(cls) -> str:
        return _PROVIDER_KEY

    @classmethod
    def supported_schema_versions(cls) -> frozenset[str]:
        return frozenset({_STATE_VERSION})

    @classmethod
    def configuration_model(cls, schema_version: str) -> type[BaseModel]:
        if schema_version != _STATE_VERSION:
            raise EnvironmentProviderError(
                f"Local Envd does not support schema version {schema_version!r}.",
                code="provider_schema_unsupported",
                category=EnvironmentProviderErrorCategory.UNSUPPORTED,
                certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
                context=EnvironmentProviderErrorContext(
                    provider_key=_PROVIDER_KEY,
                    schema_version=schema_version,
                ),
            )
        return LocalEnvdProviderConfiguration

    def lifecycle_capabilities(self, configuration: BaseModel) -> EnvironmentLifecycleCapabilities:
        _require_configuration(configuration)
        return _CAPABILITIES

    def create_provider(
        self,
        configuration: BaseModel,
        *,
        runtime: EnvironmentProviderRuntime,
    ) -> EnvironmentProvider:
        actual_configuration = _require_configuration(configuration)
        if not isinstance(runtime, LocalEnvdProviderRuntime):
            raise EnvironmentProviderError(
                "Local Envd requires LocalEnvdProviderRuntime.",
                code="provider_runtime_invalid",
                category=EnvironmentProviderErrorCategory.INVALID,
                certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                recovery_hint=EnvironmentProviderRecoveryHint.REFRESH_RUNTIME,
                context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
            )
        return LocalEnvdEnvironmentProvider(actual_configuration, runtime)


class LocalEnvdEnvironmentProvider(EnvironmentProvider):
    def __init__(
        self,
        configuration: LocalEnvdProviderConfiguration,
        runtime: LocalEnvdProviderRuntime,
    ) -> None:
        super().__init__()
        self._configuration = configuration.model_copy(deep=True)
        self._runtime = runtime
        self._identity = object()

    @property
    def lifecycle_capabilities(self) -> EnvironmentLifecycleCapabilities:
        return _CAPABILITIES

    async def create(self, *, operation: EnvironmentOperationContext) -> EnvironmentResource:
        self._require_operation(operation, EnvironmentManagementAction.CREATE, provider_key=_PROVIDER_KEY)
        configuration = await _validated_configuration(self._configuration)
        await _validate_runtime(self._runtime.executable, configuration)
        state = _build_state(
            configuration,
            resource_correlation=operation.resource_correlation,
            phase=LocalEnvdResourcePhase.RUNNING,
        )
        return LocalEnvdEnvironmentResource(
            configuration,
            state,
            runtime=self._runtime,
            provider_identity=self._identity,
        )

    async def resume(
        self,
        state: EnvironmentProviderResourceState,
        *,
        operation: EnvironmentOperationContext,
    ) -> EnvironmentResource:
        self._require_operation(operation, EnvironmentManagementAction.RESUME, provider_key=_PROVIDER_KEY)
        configuration = await _validated_configuration(self._configuration)
        _validate_state_for_phases(
            state,
            configuration=configuration,
            resource_correlation=operation.resource_correlation,
            phases=(LocalEnvdResourcePhase.RUNNING, LocalEnvdResourcePhase.PAUSED),
        )
        await _validate_runtime(self._runtime.executable, configuration)
        running = _build_state(
            configuration,
            resource_correlation=operation.resource_correlation,
            phase=LocalEnvdResourcePhase.RUNNING,
        )
        return LocalEnvdEnvironmentResource(
            configuration,
            running,
            runtime=self._runtime,
            provider_identity=self._identity,
        )

    async def pause(
        self,
        environment: EnvironmentResource,
        *,
        operation: EnvironmentOperationContext,
        mode: EnvironmentPauseMode = EnvironmentPauseMode.FULL,
    ) -> EnvironmentProviderResourceState:
        self._require_operation(operation, EnvironmentManagementAction.PAUSE, provider_key=_PROVIDER_KEY)
        if mode is not EnvironmentPauseMode.FILESYSTEM:
            raise EnvironmentProviderError(
                "Local Envd supports only filesystem pause.",
                code="provider_action_unsupported",
                category=EnvironmentProviderErrorCategory.UNSUPPORTED,
                certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                context=_operation_context(operation),
            )
        if (
            not isinstance(environment, LocalEnvdEnvironmentResource)
            or environment._provider_identity is not self._identity
        ):
            raise _state_error("Local Envd pause requires a Resource created by this Provider instance.", operation)
        environment._require_entered()
        state_data = _validate_state_for_phases(
            environment.state,
            configuration=environment._configuration,
            resource_correlation=operation.resource_correlation,
            phases=(LocalEnvdResourcePhase.RUNNING, LocalEnvdResourcePhase.PAUSED),
        )
        if state_data.phase is LocalEnvdResourcePhase.PAUSED:
            return environment.state
        paused = _build_state(
            environment._configuration,
            resource_correlation=operation.resource_correlation,
            phase=LocalEnvdResourcePhase.PAUSED,
        )
        await environment._pause(paused)
        return paused

    async def destroy(
        self,
        state: EnvironmentProviderResourceState,
        *,
        operation: EnvironmentOperationContext,
    ) -> None:
        self._require_operation(operation, EnvironmentManagementAction.DESTROY, provider_key=_PROVIDER_KEY)
        configuration = await _validated_configuration(self._configuration)
        _validate_state_for_phases(
            state,
            configuration=configuration,
            resource_correlation=operation.resource_correlation,
            phases=(LocalEnvdResourcePhase.RUNNING, LocalEnvdResourcePhase.PAUSED),
        )

    async def reconcile(
        self,
        operation: EnvironmentOperationContext,
        *,
        last_known_state: EnvironmentProviderResourceState | None,
    ) -> EnvironmentReconciliationResult:
        self._require_reconciliation_operation(operation, provider_key=_PROVIDER_KEY)
        if operation.action is EnvironmentManagementAction.DESTROY:
            if last_known_state is None:
                return _unknown_reconciliation(operation, "destroy_state_missing")
            configuration = await _validated_configuration(self._configuration)
            _validate_state_for_phases(
                last_known_state,
                configuration=configuration,
                resource_correlation=operation.resource_correlation,
                phases=(LocalEnvdResourcePhase.RUNNING, LocalEnvdResourcePhase.PAUSED),
            )
            return EnvironmentReconciliationResult(
                operation_id=operation.operation_id,
                phase=EnvironmentReconciliationPhase.ABSENT,
                evidence={"logical_resource": "detached"},
            )

        try:
            configuration = await _validated_configuration(self._configuration)
            if operation.action is EnvironmentManagementAction.PAUSE:
                if last_known_state is None:
                    return _unknown_reconciliation(operation, "paused_state_missing")
                state_data = _validate_state_for_phases(
                    last_known_state,
                    configuration=configuration,
                    resource_correlation=operation.resource_correlation,
                    phases=(LocalEnvdResourcePhase.RUNNING, LocalEnvdResourcePhase.PAUSED),
                )
                if state_data.phase is LocalEnvdResourcePhase.RUNNING:
                    return _unknown_reconciliation(operation, "pause_completion_unconfirmed")
                return EnvironmentReconciliationResult(
                    operation_id=operation.operation_id,
                    phase=EnvironmentReconciliationPhase.PAUSED,
                    state=last_known_state,
                    evidence={"logical_resource": "paused"},
                )
            await asyncio.to_thread(_validate_runtime_executable, self._runtime.executable)
        except EnvironmentProviderError as error:
            if error.category in {
                EnvironmentProviderErrorCategory.MISSING,
                EnvironmentProviderErrorCategory.DENIED,
                EnvironmentProviderErrorCategory.UNAVAILABLE,
                EnvironmentProviderErrorCategory.TIMEOUT,
            }:
                return _unknown_reconciliation(operation, error.code)
            raise

        running = _build_state(
            configuration,
            resource_correlation=operation.resource_correlation,
            phase=LocalEnvdResourcePhase.RUNNING,
        )
        if last_known_state is not None:
            if operation.action is EnvironmentManagementAction.RESUME:
                _validate_state_for_phases(
                    last_known_state,
                    configuration=configuration,
                    resource_correlation=operation.resource_correlation,
                    phases=(LocalEnvdResourcePhase.PAUSED, LocalEnvdResourcePhase.RUNNING),
                )
            else:
                _validate_state(last_known_state, expected=running)
        return _unknown_reconciliation(operation, "runtime_compatibility_unconfirmed")


class LocalEnvdEnvironmentResource(EnvironmentResource):
    def __init__(
        self,
        configuration: LocalEnvdProviderConfiguration,
        state: EnvironmentProviderResourceState,
        *,
        runtime: LocalEnvdProviderRuntime,
        provider_identity: object,
    ) -> None:
        super().__init__()
        self._configuration = configuration
        self._state = state
        self._runtime = runtime
        self._provider_identity = provider_identity
        self._attachment_sequence = 0
        self._active_attachments = 0
        self._admitting = False
        self._lock = asyncio.Lock()
        self._allocation: AbstractAsyncContextManager[Path] | None = None
        self._allocation_entered = False
        self._allocation_root: Path | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._carrier: StdioEIPCarrier | None = None
        self._stderr_file: Any | None = None
        self._cleanup_task: asyncio.Task[BaseException | None] | None = None

    @property
    def state(self) -> EnvironmentProviderResourceState:
        return self._state

    async def _enter_scope(self) -> None:
        allocation = self._runtime.allocate_private_runtime()
        if not isinstance(allocation, AbstractAsyncContextManager):
            raise _runtime_failure("Local Envd private runtime allocator returned an invalid context manager.")
        self._allocation = allocation
        try:
            root = await allocation.__aenter__()
            self._allocation_entered = True
            self._allocation_root = await asyncio.to_thread(_prepare_allocation_root, root)
            runtime_dir, config_path, stderr_path, ready_path = await asyncio.to_thread(
                _write_private_bootstrap,
                self._allocation_root,
                self._configuration,
            )
            self._stderr_file = await asyncio.to_thread(open, stderr_path, "wb")
            process_environment = _daemon_environment(
                environment_id=self._configuration.environment_id,
                runtime_dir=runtime_dir,
                ready_file=ready_path,
            )
            subprocess_options: dict[str, Any] = {}
            if os.name == "posix":
                subprocess_options["start_new_session"] = True
            elif os.name == "nt":  # pragma: no cover - exercised on Windows
                subprocess_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            self._process = await asyncio.create_subprocess_exec(
                str(self._runtime.executable),
                "--config",
                str(config_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=self._stderr_file,
                env=process_environment,
                **subprocess_options,
            )
            self._carrier = StdioEIPCarrier(
                self._process,
                max_request_bytes=_DAEMON_MAX_REQUEST_BYTES,
                max_response_bytes=_DAEMON_MAX_RESPONSE_BYTES,
                max_transfer_frame_bytes=_DAEMON_MAX_TRANSFER_FRAME_BYTES,
            )
            await _wait_for_daemon_readiness(self._process, ready_path, stderr_path)
            async with self._lock:
                self._admitting = True
        except BaseException as entry_error:
            cleanup_error = await self._cleanup_local_runtime()
            if isinstance(entry_error, asyncio.CancelledError):
                if cleanup_error is not None:
                    entry_error.add_note(f"Local Envd entry cleanup also failed: {cleanup_error!r}")
                raise
            primary_error = _normalize_resource_entry_error(entry_error)
            if cleanup_error is not None:
                raise BaseExceptionGroup(
                    "Local Envd Resource entry and cleanup failed",
                    [primary_error, cleanup_error],
                ) from None
            if primary_error is entry_error:
                raise
            raise primary_error from entry_error

    @asynccontextmanager
    async def acquire_attachment(self) -> AsyncGenerator[EnvironmentRuntimeAttachment]:
        self._require_entered()
        async with self._lock:
            carrier = self._carrier
            if self._active_attachments:
                raise EnvironmentProviderError(
                    "Local Envd Resource already has an active attachment.",
                    code="provider_attachment_conflict",
                    category=EnvironmentProviderErrorCategory.CONFLICT,
                    certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                    context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
                )
            if carrier is None or not carrier.is_available:
                self._admitting = False
                state_data = _decode_state(self._state)
                if state_data.phase is LocalEnvdResourcePhase.RUNNING:
                    raise _runtime_failure("Local Envd Resource carrier is unavailable.")
                raise EnvironmentProviderError(
                    "Local Envd Resource is paused and cannot admit attachments.",
                    code="provider_attachment_conflict",
                    category=EnvironmentProviderErrorCategory.CONFLICT,
                    certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                    context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
                )
            if not self._admitting:
                raise EnvironmentProviderError(
                    "Local Envd Resource attachment admission is closed.",
                    code="provider_attachment_conflict",
                    category=EnvironmentProviderErrorCategory.CONFLICT,
                    certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                    context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
                )
            self._attachment_sequence += 1
            self._active_attachments = 1
            source = carrier.lease()
            attachment = EIPEnvironmentAttachment(
                attachment_id=f"attachment-{self._attachment_sequence}",
                environment_id=self._configuration.environment_id,
                session_source=source,
            )
        primary_error: BaseException | None = None
        try:
            yield attachment
        except BaseException as error:
            primary_error = error
            raise
        finally:
            release_error: BaseException | None = None
            try:
                await source.discard()
            except BaseException as error:
                release_error = _normalize_attachment_release_error(error)
            finally:
                async with self._lock:
                    self._active_attachments = 0
                    if self._carrier is None or not self._carrier.is_available:
                        self._admitting = False
            if release_error is not None:
                if isinstance(primary_error, asyncio.CancelledError):
                    primary_error.add_note(f"Local Envd attachment release also failed: {release_error!r}")
                elif primary_error is not None:
                    raise BaseExceptionGroup(
                        "Local Envd attachment use and release failed",
                        [primary_error, release_error],
                    ) from None
                else:
                    raise release_error

    async def _pause(self, paused: EnvironmentProviderResourceState) -> None:
        self._require_entered()
        async with self._lock:
            if self._active_attachments:
                raise EnvironmentProviderError(
                    "Local Envd cannot pause with an active attachment scope.",
                    code="provider_conflict",
                    category=EnvironmentProviderErrorCategory.CONFLICT,
                    certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                    context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
                )
            self._admitting = False
        cleanup_error = await self._cleanup_local_runtime()
        if cleanup_error is not None:
            raise cleanup_error
        self._state = paused

    async def _exit_scope(self) -> None:
        async with self._lock:
            self._admitting = False
            active = self._active_attachments
        cleanup_error = await self._cleanup_local_runtime()
        if active:
            active_error = EnvironmentProviderError(
                "Local Envd EnvironmentResource closed with active attachment scopes.",
                code="provider_cleanup_failed",
                category=EnvironmentProviderErrorCategory.CLEANUP,
                certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
                context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
                details={"active_attachment_count": active},
            )
            if cleanup_error is not None:
                raise BaseExceptionGroup(
                    "Local Envd attachment and runtime cleanup failed",
                    [active_error, cleanup_error],
                )
            raise active_error
        if cleanup_error is not None:
            raise cleanup_error

    async def _cleanup_local_runtime(self) -> BaseException | None:
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(
                self._perform_cleanup(),
                name="local-envd-resource-cleanup",
            )
        return await _await_resource_cleanup(self._cleanup_task)

    async def _perform_cleanup(self) -> BaseException | None:
        errors: list[BaseException] = []
        carrier, process = self._carrier, self._process
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
            "Local Envd could not prove complete process/private-runtime cleanup.",
            code="provider_cleanup_failed",
            category=EnvironmentProviderErrorCategory.CLEANUP,
            certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
            context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
            details={"failure_count": len(errors)},
        )
        for error in errors:
            wrapped.add_note(repr(error))
        return wrapped


def _require_configuration(configuration: BaseModel) -> LocalEnvdProviderConfiguration:
    if not isinstance(configuration, LocalEnvdProviderConfiguration):
        raise EnvironmentProviderError(
            "Local Envd requires LocalEnvdProviderConfiguration.",
            code="provider_spec_invalid",
            category=EnvironmentProviderErrorCategory.INVALID,
            certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
            recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
            context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
        )
    return configuration


async def _validated_configuration(
    configuration: LocalEnvdProviderConfiguration,
) -> LocalEnvdProviderConfiguration:
    return await asyncio.to_thread(_canonical_configuration, configuration)


def _canonical_configuration(
    configuration: LocalEnvdProviderConfiguration,
) -> LocalEnvdProviderConfiguration:
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


async def _validate_runtime(executable: Path, configuration: LocalEnvdProviderConfiguration) -> None:
    await asyncio.to_thread(_validate_runtime_executable, executable)
    expected_version = f"agent-envd {_client_release_identity()}\n".encode()
    version = await _run_checked_subprocess(executable, "--version", environment=None)
    if version[0] != expected_version or version[1]:
        raise _runtime_failure(f"Local Envd agent-envd release does not match a13n-envd-client {envd_client_version}.")

    probe_environment = {name: value for name, value in os.environ.items() if not name.startswith("AGENT_ENVD_")}
    probe_environment["AGENT_ENVD_EXECUTION_ISOLATION"] = "required"
    probe_environment["AGENT_ENVD_EXECUTION_NETWORK"] = configuration.execution_network.value
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
        raise _runtime_failure(f"Local Envd agent-envd executable does not exist: {executable}") from error
    except PermissionError as error:
        raise _runtime_failure(f"Local Envd agent-envd executable is not accessible: {executable}") from error
    except (OSError, ValueError) as error:
        raise _runtime_failure(f"Local Envd agent-envd executable cannot be inspected: {executable}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise _runtime_failure(f"Local Envd agent-envd executable is not a regular file: {executable}")
    if not os.access(executable, os.X_OK):
        raise _runtime_failure(f"Local Envd agent-envd executable is not executable: {executable}")


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
        )
        async with asyncio.timeout(_SUBPROCESS_TIMEOUT_SECONDS):
            stdout, stderr = await process.communicate()
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
    except (OSError, subprocess.SubprocessError) as error:
        if process is not None:
            await _stop_validation_process(process)
        raise _runtime_failure("Local Envd runtime validation could not execute agent-envd.") from error
    assert process is not None
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[:512]
        raise _runtime_failure(f"Local Envd runtime validation failed with code {process.returncode}: {detail}")
    return stdout, stderr


async def _stop_validation_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()
    await asyncio.shield(process.wait())


def _build_state(
    configuration: LocalEnvdProviderConfiguration,
    *,
    resource_correlation: str,
    phase: LocalEnvdResourcePhase,
) -> EnvironmentProviderResourceState:
    payload = configuration.model_dump(mode="json")
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    data = LocalEnvdProviderStateData(
        environment_id=configuration.environment_id,
        resource_correlation=resource_correlation,
        configuration_fingerprint=f"sha256:{hashlib.sha256(encoded).hexdigest()}",
        phase=phase,
    )
    return EnvironmentProviderResourceState(
        provider_key=_PROVIDER_KEY,
        state_version=_STATE_VERSION,
        data=data.model_dump(mode="json"),
    )


def _validate_state(
    state: EnvironmentProviderResourceState,
    *,
    expected: EnvironmentProviderResourceState,
) -> None:
    _decode_state(state)
    if state != expected:
        raise _state_error("Local Envd state does not match this Provider configuration and resource.")


def _validate_state_for_phases(
    state: EnvironmentProviderResourceState,
    *,
    configuration: LocalEnvdProviderConfiguration,
    resource_correlation: str,
    phases: tuple[LocalEnvdResourcePhase, ...],
) -> LocalEnvdProviderStateData:
    data = _decode_state(state)
    expected_states = tuple(
        _build_state(configuration, resource_correlation=resource_correlation, phase=phase) for phase in phases
    )
    if state not in expected_states:
        raise _state_error("Local Envd state does not match this Provider configuration and resource.")
    return data


def _decode_state(state: EnvironmentProviderResourceState) -> LocalEnvdProviderStateData:
    if state.provider_key != _PROVIDER_KEY or state.state_version != _STATE_VERSION:
        raise _state_error("Local Envd state envelope is incompatible.")
    try:
        return LocalEnvdProviderStateData.model_validate(state.data)
    except ValidationError as error:
        raise _state_error("Local Envd state data is invalid.") from error


def _write_private_bootstrap(
    allocation_root: Path,
    configuration: LocalEnvdProviderConfiguration,
) -> tuple[Path, Path, Path, Path]:
    runtime_dir = allocation_root / "envd-runtime"
    runtime_dir.mkdir(mode=0o700)
    config_path = allocation_root / "agent-envd.json"
    stderr_path = allocation_root / "agent-envd.stderr.log"
    ready_path = runtime_dir / ".agent-envd.ready"
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
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(config_path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        stream.flush()
        os.fsync(stream.fileno())
    return runtime_dir, config_path, stderr_path, ready_path


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


def _daemon_environment(*, environment_id: str, runtime_dir: Path, ready_file: Path) -> dict[str, str]:
    environment = {name: value for name, value in os.environ.items() if not name.startswith("AGENT_ENVD_")}
    environment.update(
        {
            "AGENT_ENVD_ENVIRONMENT_ID": environment_id,
            "AGENT_ENVD_RUNTIME_DIR": str(runtime_dir),
            "AGENT_ENVD_READY_FILE": str(ready_file),
            "AGENT_ENVD_TRANSPORT": "stdio",
        }
    )
    return environment


async def _await_resource_cleanup(
    cleanup_task: asyncio.Task[BaseException | None],
) -> BaseException | None:
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


async def _wait_for_daemon_readiness(
    process: asyncio.subprocess.Process,
    ready_path: Path,
    stderr_path: Path,
) -> None:
    try:
        async with asyncio.timeout(_STARTUP_TIMEOUT_SECONDS):
            while True:
                if process.returncode is not None:
                    detail = await asyncio.to_thread(_read_startup_error, stderr_path)
                    raise _runtime_failure(
                        f"Local Envd daemon exited during startup with code {process.returncode}: {detail}"
                    )
                if await asyncio.to_thread(_has_ready_marker, ready_path):
                    try:
                        return_code = await asyncio.wait_for(
                            process.wait(),
                            timeout=_STARTUP_STABILITY_SECONDS,
                        )
                    except TimeoutError:
                        return
                    detail = await asyncio.to_thread(_read_startup_error, stderr_path)
                    raise _runtime_failure(
                        f"Local Envd daemon exited after reporting readiness with code {return_code}: {detail}"
                    )
                await asyncio.sleep(_STARTUP_POLL_SECONDS)
    except TimeoutError as error:
        detail = await asyncio.to_thread(_read_startup_error, stderr_path)
        raise _runtime_failure(
            f"Local Envd daemon did not become ready before its launch deadline: {detail}"
        ) from error


def _has_ready_marker(path: Path) -> bool:
    try:
        metadata = path.lstat()
        return (
            stat.S_ISREG(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and path.read_bytes() == _READY_MARKER_CONTENT
        )
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as error:
        raise _runtime_failure("Local Envd readiness marker could not be inspected.") from error


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
                await asyncio.sleep(_STARTUP_POLL_SECONDS)
            return
    except TimeoutError:
        pass
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        return
    async with asyncio.timeout(_TERMINATE_GRACE_SECONDS):
        while _posix_process_group_exists(process_group):
            await asyncio.sleep(_STARTUP_POLL_SECONDS)


def _posix_process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _read_startup_error(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-1024:].strip() or "no diagnostic output"
    except OSError:
        return "diagnostic output unavailable"


def _normalize_resource_entry_error(error: BaseException) -> BaseException:
    if isinstance(error, EnvironmentProviderError):
        return error
    wrapped = _runtime_failure("Local Envd Resource entry failed before attachment admission.")
    wrapped.add_note(repr(error))
    return wrapped


def _normalize_attachment_release_error(error: BaseException) -> BaseException:
    if isinstance(error, (asyncio.CancelledError, EnvironmentProviderError)):
        return error
    wrapped = _runtime_failure("Local Envd attachment release found an unavailable carrier.")
    wrapped.add_note(repr(error))
    return wrapped


def _unknown_reconciliation(
    operation: EnvironmentOperationContext,
    reason: str,
) -> EnvironmentReconciliationResult:
    return EnvironmentReconciliationResult(
        operation_id=operation.operation_id,
        phase=EnvironmentReconciliationPhase.UNKNOWN,
        evidence={"reason": reason},
    )


def _operation_context(operation: EnvironmentOperationContext) -> EnvironmentProviderErrorContext:
    return EnvironmentProviderErrorContext(
        provider_key=_PROVIDER_KEY,
        action=operation.action,
        operation_id=operation.operation_id,
        resource_correlation=operation.resource_correlation,
    )


def _state_error(
    description: str,
    operation: EnvironmentOperationContext | None = None,
) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        description,
        code="provider_state_invalid",
        category=EnvironmentProviderErrorCategory.INVALID,
        certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
        recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
        context=_operation_context(operation)
        if operation is not None
        else EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY, state_version=_STATE_VERSION),
    )


def _spec_error(description: str) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        description,
        code="provider_spec_invalid",
        category=EnvironmentProviderErrorCategory.INVALID,
        certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
        recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
        context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY, schema_version=_STATE_VERSION),
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
