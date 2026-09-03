from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any

from a13n_envd_client import EIPSession
from a13n_envd_client.eip import v1 as eip
from pydantic import ValidationError

from ..attachments import HttpEIPSessionSource
from ..eip._common import invoke
from ..eip.files import EIPFileOperator
from ..eip.output import EIPOutputOperations, EIPOutputRegistry
from ..eip.processes import (
    EIPPortOperations,
    EIPProcessOperations,
    EIPShellOperations,
    _ProcessConversions,
)
from ..errors import (
    EnvironmentProviderError,
)
from ..management import Environment
from ..models import (
    EnvironmentAction,
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentMountDescriptor,
    EnvironmentOperationFamily,
    EnvironmentPermissionSet,
    EnvironmentState,
)
from ..operations import EnvironmentOperations
from ._errors import (
    cleanup_failure as _cleanup_failure,
)
from ._errors import (
    conflict_failure as _conflict_failure,
)
from ._errors import (
    missing_failure as _missing_failure,
)
from ._errors import (
    runtime_failure as _runtime_failure,
)
from ._errors import (
    state_failure as _state_failure,
)
from ._errors import (
    unknown_failure as _unknown_failure,
)
from .configuration import (
    DockerBindMountSource,
    DockerImagePullPolicy,
    DockerMountConfiguration,
    DockerProviderConfiguration,
    DockerProviderStateData,
    DockerVolumeMountSource,
)
from .runtime import (
    DockerBootstrapAllocation,
    DockerBootstrapMaterial,
    DockerBootstrapStoreError,
    DockerContainerInspection,
    DockerContainerSpec,
    DockerEngineError,
    DockerEngineMount,
    DockerImageInspection,
    DockerProviderRuntime,
)

_PROVIDER_KEY = "a13n.docker"
_STATE_VERSION = "1"
_EIP_CONTAINER_PORT = 8787
_BOOTSTRAP_CONTAINER_PATH = "/run/a13n/bootstrap"
_CONFIG_CONTAINER_PATH = f"{_BOOTSTRAP_CONTAINER_PATH}/envd.json"
_CREDENTIAL_CONTAINER_PATH = f"{_BOOTSTRAP_CONTAINER_PATH}/credential"
_RUNTIME_CONTAINER_PATH = "/home/sandbox/.local/state/agent-envd"
_COMMAND = ("agent-envd", "--config", _CONFIG_CONTAINER_PATH)
_REQUIRED_ENVIRONMENT = {
    "AGENT_ENVD_TRANSPORT": "http",
    "AGENT_ENVD_HTTP_BIND": f"0.0.0.0:{_EIP_CONTAINER_PORT}",
    "AGENT_ENVD_HTTP_CREDENTIAL_FILE": _CREDENTIAL_CONTAINER_PATH,
    "AGENT_ENVD_HTTP_PLAINTEXT_SCOPE": "provider_private_link",
    "AGENT_ENVD_RUNTIME_DIR": _RUNTIME_CONTAINER_PATH,
    "AGENT_ENVD_EXECUTION_ISOLATION": "disabled",
    "AGENT_ENVD_EXECUTION_NETWORK": "host",
}
_LABEL_PROVIDER = "io.a13n.environment-provider"
_LABEL_STATE = "io.a13n.environment-provider.state"
_LABEL_ENVIRONMENT = "io.a13n.environment-id"
_LABEL_BOOTSTRAP = "io.a13n.bootstrap-correlation"
_LABEL_FINGERPRINT = "io.a13n.configuration-fingerprint"
_LABEL_CREATE = "io.a13n.create-correlation"
_REQUIRED_EIP_METHODS = frozenset({"environment.describe", "environment.readiness", "session.close"})
_METHOD_ACTIONS: dict[str, tuple[EnvironmentAction, ...]] = {
    "file.stat": (EnvironmentAction.FILE_STAT,),
    "file.read_text": (EnvironmentAction.FILE_READ_TEXT,),
    "file.open_reader": (EnvironmentAction.FILE_READ_BYTES,),
    "file.write_text": (EnvironmentAction.FILE_WRITE_TEXT,),
    "file.patch_text": (EnvironmentAction.FILE_PATCH_TEXT,),
    "file.list": (EnvironmentAction.FILE_LIST,),
    "file.find": (EnvironmentAction.FILE_QUERY,),
    "file.search": (EnvironmentAction.FILE_SEARCH_TEXT,),
    "file.mkdir": (EnvironmentAction.FILE_MKDIR,),
    "file.move": (EnvironmentAction.FILE_MOVE,),
    "file.remove": (EnvironmentAction.FILE_REMOVE,),
    "file.open_writer": (EnvironmentAction.FILE_WRITE_BYTES,),
    "file.copy": (EnvironmentAction.FILE_COPY_SOURCE, EnvironmentAction.FILE_COPY_DESTINATION),
    "shell.exec": (EnvironmentAction.SHELL_EXEC,),
    "process.start": (EnvironmentAction.PROCESS_START,),
    "process.inspect": (EnvironmentAction.PROCESS_INSPECT,),
    "process.write_stdin": (EnvironmentAction.PROCESS_WRITE_STDIN,),
    "process.close_stdin": (EnvironmentAction.PROCESS_CLOSE_STDIN,),
    "process.signal": (EnvironmentAction.PROCESS_SIGNAL,),
    "process.wait": (EnvironmentAction.PROCESS_WAIT,),
    "process.kill": (EnvironmentAction.PROCESS_KILL,),
    "process.release": (EnvironmentAction.PROCESS_RELEASE,),
    "output.read": (EnvironmentAction.OUTPUT_READ,),
    "output.release": (EnvironmentAction.OUTPUT_RELEASE,),
    "port.inspect": (EnvironmentAction.PORT_INSPECT,),
    "port.wait": (EnvironmentAction.PORT_WAIT,),
}


class _DockerEIPSession:
    def __init__(self, *, environment_id: str, runtime: DockerProviderRuntime) -> None:
        self._environment_id = environment_id
        self._runtime = runtime
        self._descriptor: EnvironmentDescriptor | None = None
        self._availability = EnvironmentAvailability(status="preparing")
        self._operations = EnvironmentOperations()
        self._session_context: AbstractAsyncContextManager[EIPSession] | None = None
        self._session: EIPSession | None = None
        self._outputs: EIPOutputRegistry | None = None
        self._conversions: _ProcessConversions | None = None

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

    async def _open_eip(
        self,
        inspection: DockerContainerInspection,
        allocation: DockerBootstrapAllocation,
        *,
        mount_id: str,
    ) -> None:
        if (
            inspection.status != "running"
            or not inspection.eip_route_exact
            or inspection.eip_host_ip != "127.0.0.1"
            or inspection.eip_host_port is None
        ):
            raise _conflict_failure("Docker target has no exact active Host-loopback EIP route.")
        source = HttpEIPSessionSource(
            f"http://127.0.0.1:{inspection.eip_host_port}",
            allocation.material.credential,
            initialization_timeout=10.0,
            request_timeout=30.0,
            allow_plaintext_private_link=True,
        )
        context = source.open_session(
            expected_environment_id=self.environment_id,
            required_methods=_REQUIRED_EIP_METHODS,
        )
        try:
            session = await context.__aenter__()
            readiness = await invoke(session.readiness())
            if not readiness.ready:
                raise EnvironmentError(
                    "Docker EIP environment is not ready",
                    code="environment_unavailable",
                    retry_hint="new_run",
                )
            descriptor = _convert_descriptor(session.descriptor)
            operations, outputs, conversions = _compose_operations(
                session,
                environment_id=self.environment_id,
                mount_id=mount_id,
                descriptor=descriptor,
            )
        except BaseException as error:
            try:
                await context.__aexit__(type(error), error, error.__traceback__)
            except BaseException as cleanup_error:
                error.add_note(f"Docker EIP cleanup also failed: {cleanup_error!r}")
            if isinstance(error, asyncio.CancelledError | EnvironmentProviderError):
                raise
            raise _runtime_failure("Docker EIP initialization or readiness failed.") from error
        self._session_context = context
        self._session = session
        self._descriptor = descriptor
        self._operations = operations
        self._outputs = outputs
        self._conversions = conversions
        self._availability = EnvironmentAvailability(
            status="available",
            ready_families=descriptor.operation_families,
        )

    async def _ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        missing = operations - self.descriptor.operation_families
        if missing:
            raise EnvironmentError(
                "Docker EIP does not expose the requested operation family",
                code="environment_unsupported",
            )
        if self._session is None:
            raise EnvironmentError("Docker EIP session is closed", code="environment_unavailable")
        readiness = await invoke(self._session.readiness())
        if not readiness.ready:
            self._availability = EnvironmentAvailability(status="unavailable")
            raise EnvironmentError(
                "Docker EIP environment is not ready",
                code="environment_unavailable",
                retry_hint="new_run",
            )

    async def _close(self) -> None:
        self._availability = EnvironmentAvailability(status="unavailable")
        errors: list[BaseException] = []
        if self._conversions is not None:
            try:
                await self._conversions.cleanup_owned()
            except BaseException as error:
                errors.append(error)
        if self._outputs is not None:
            try:
                await self._outputs.cleanup_pending()
            except BaseException as error:
                errors.append(error)
        if self._session_context is not None:
            try:
                await self._session_context.__aexit__(None, None, None)
            except BaseException as error:
                errors.append(error)
        self._session_context = None
        self._session = None
        self._outputs = None
        self._conversions = None
        self._operations = EnvironmentOperations()
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Docker local cleanup failed", errors)


class DockerEnvironment(_DockerEIPSession, Environment):
    """Fresh single-use adapter for one exact Docker container."""

    def __init__(
        self,
        configuration: DockerProviderConfiguration,
        state: EnvironmentState | None,
        *,
        state_data: DockerProviderStateData | None,
        runtime: DockerProviderRuntime,
    ) -> None:
        Environment.__init__(self, state.model_copy(deep=True) if state is not None else None)
        _DockerEIPSession.__init__(self, environment_id=configuration.environment_id, runtime=runtime)
        self._configuration = configuration.model_copy(deep=True)
        self._state_data = state_data.model_copy(deep=True) if state_data is not None else None

    async def _enter(
        self,
        *,
        thread_id: str,
        run_id: str,
        agent_instance_id: str,
        mount_id: str,
        host_refs: Mapping[str, str],
    ) -> None:
        del host_refs
        configuration = await _canonical_configuration(self._configuration)
        await _engine_call(self._runtime.engine.validate_local_topology())
        for mount in _configured_engine_mounts(configuration):
            await _engine_call(self._runtime.engine.validate_mount(mount))

        if self._state_data is None:
            state, inspection, allocation = await self._create_target(
                configuration,
                correlation_seed=(thread_id, run_id, agent_instance_id, mount_id),
            )
        else:
            state, inspection, allocation = await self._reenter_target(
                configuration,
                self._state_data,
                correlation_seed=(thread_id, run_id, agent_instance_id, mount_id),
            )
        self._state_data = state
        await self._open_eip(inspection, allocation, mount_id=mount_id)

    async def _create_target(
        self,
        configuration: DockerProviderConfiguration,
        *,
        correlation_seed: tuple[str, ...],
        replacing: DockerProviderStateData | None = None,
    ) -> tuple[DockerProviderStateData, DockerContainerInspection, DockerBootstrapAllocation]:
        image = await _resolve_image(self._runtime, configuration)
        _validate_image(image)
        fingerprint = _configuration_fingerprint(self._configuration)
        create_correlation = _correlation(
            "create", *correlation_seed, replacing.create_correlation if replacing else "new"
        )
        bootstrap_correlation = _correlation("bootstrap", create_correlation)
        labels = _labels(
            configuration,
            bootstrap_correlation=bootstrap_correlation,
            configuration_fingerprint=fingerprint,
            create_correlation=create_correlation,
        )

        allocation = await _store_recover(self._runtime, bootstrap_correlation)
        created_allocation = False
        if allocation is None:
            allocation = await _store_create(
                self._runtime,
                bootstrap_correlation,
                _new_bootstrap_material(configuration, fingerprint),
            )
            created_allocation = True
        else:
            _validate_bootstrap(allocation, configuration, fingerprint)

        matches = await _engine_call(self._runtime.engine.find_containers(labels))
        if len(matches) > 1:
            raise _unknown_failure("Docker create correlation is ambiguous.")
        if matches:
            inspection = matches[0]
            _validate_inspection(
                inspection,
                configuration=configuration,
                image_id=image.image_id,
                labels=labels,
                allocation=allocation,
                require_route=inspection.status == "running",
            )
            state = _state_data(
                configuration,
                container_id=inspection.container_id,
                image_id=inspection.image_id,
                bootstrap_correlation=bootstrap_correlation,
                configuration_fingerprint=fingerprint,
                create_correlation=create_correlation,
            )
            self._publish_state(state)
            inspection, allocation = await self._start_if_needed(configuration, state, inspection, allocation)
            return state, inspection, allocation

        bootstrap_mount = DockerEngineMount(
            type="bind",
            source=str(allocation.directory),
            target=_BOOTSTRAP_CONTAINER_PATH,
            read_only=True,
        )
        await _engine_call(self._runtime.engine.validate_mount(bootstrap_mount))
        spec = _container_spec(
            configuration,
            image_id=image.image_id,
            labels=labels,
            mounts=(*_configured_engine_mounts(configuration), bootstrap_mount),
        )
        dispatched = False
        try:
            dispatched = True
            container_id = await self._runtime.engine.create_container(spec)
            state = _state_data(
                configuration,
                container_id=container_id,
                image_id=image.image_id,
                bootstrap_correlation=bootstrap_correlation,
                configuration_fingerprint=fingerprint,
                create_correlation=create_correlation,
            )
            self._publish_state(state)
            await self._runtime.engine.start_container(container_id)
            inspection = await _engine_call(self._runtime.engine.inspect_container(container_id))
            if inspection is None:
                raise _unknown_failure("Docker container disappeared after create.")
            _validate_inspection(
                inspection,
                configuration=configuration,
                image_id=image.image_id,
                labels=labels,
                allocation=allocation,
                require_route=True,
            )
            if inspection.status != "running":
                raise _unknown_failure("Docker container did not reach running state.")
            return state, inspection, allocation
        except asyncio.CancelledError:
            if created_allocation and not dispatched:
                await _remove_bootstrap(self._runtime, bootstrap_correlation)
            raise
        except DockerEngineError as error:
            if created_allocation and not dispatched and not error.dispatched:
                await _remove_bootstrap(self._runtime, bootstrap_correlation)
            if dispatched or error.dispatched:
                raise _unknown_failure("Docker create or start outcome is unknown.") from error
            raise _runtime_failure("Docker container creation failed.") from error
        except BaseException:
            if created_allocation and not dispatched:
                await _remove_bootstrap(self._runtime, bootstrap_correlation)
            raise

    async def _reenter_target(
        self,
        configuration: DockerProviderConfiguration,
        state: DockerProviderStateData,
        *,
        correlation_seed: tuple[str, ...],
    ) -> tuple[DockerProviderStateData, DockerContainerInspection, DockerBootstrapAllocation]:
        inspection = await _engine_call(self._runtime.engine.inspect_container(state.container_id))
        if inspection is None:
            matches = await _engine_call(self._runtime.engine.find_containers(_labels_from_state(configuration, state)))
            if matches:
                raise _unknown_failure("Docker exact target is absent but its correlation is still present.")
            return await self._create_target(
                configuration,
                correlation_seed=(*correlation_seed, "replacement"),
                replacing=state,
            )

        allocation = await _store_recover(self._runtime, state.bootstrap_correlation)
        if allocation is None:
            raise _missing_failure("Docker bootstrap allocation is unavailable for the existing target.")
        _validate_bootstrap(allocation, configuration, state.configuration_fingerprint)
        _validate_inspection(
            inspection,
            configuration=configuration,
            image_id=state.image_id,
            labels=_labels_from_state(configuration, state),
            allocation=allocation,
            require_route=inspection.status == "running",
        )
        inspection, allocation = await self._start_if_needed(configuration, state, inspection, allocation)
        return state, inspection, allocation

    async def _start_if_needed(
        self,
        configuration: DockerProviderConfiguration,
        state: DockerProviderStateData,
        inspection: DockerContainerInspection,
        allocation: DockerBootstrapAllocation,
    ) -> tuple[DockerContainerInspection, DockerBootstrapAllocation]:
        if inspection.status == "running":
            return inspection, allocation
        if inspection.status not in {"created", "exited"}:
            raise _conflict_failure("Docker container state is not safely re-enterable.")
        allocation = await _store_replace(
            self._runtime,
            state.bootstrap_correlation,
            _new_bootstrap_material(configuration, state.configuration_fingerprint),
        )
        try:
            await self._runtime.engine.start_container(state.container_id)
            current = await _engine_call(self._runtime.engine.inspect_container(state.container_id))
        except DockerEngineError as error:
            raise _unknown_failure("Docker container start outcome is unknown.") from error
        if current is None:
            raise _unknown_failure("Docker container disappeared after start.")
        _validate_inspection(
            current,
            configuration=configuration,
            image_id=state.image_id,
            labels=_labels_from_state(configuration, state),
            allocation=allocation,
            require_route=True,
        )
        if current.status != "running":
            raise _unknown_failure("Docker container did not reach running state.")
        return current, allocation

    def _publish_state(self, state: DockerProviderStateData) -> None:
        envelope = EnvironmentState(
            provider_key=_PROVIDER_KEY,
            state_version=_STATE_VERSION,
            state=state.model_dump(mode="json"),
        )
        self._state_data = state
        self._cache_state(envelope)

    async def _destroy(self) -> None:
        state = self._state_data
        if state is None:
            raise _state_failure("Docker destroy requires state for one exact target.")
        configuration = await _canonical_configuration(self._configuration)
        await _engine_call(self._runtime.engine.validate_local_topology())
        inspection = await _engine_call(self._runtime.engine.inspect_container(state.container_id))
        if inspection is not None:
            allocation = await _store_recover(self._runtime, state.bootstrap_correlation)
            if allocation is None:
                raise _missing_failure("Docker bootstrap allocation is unavailable for the existing target.")
            _validate_bootstrap(allocation, configuration, state.configuration_fingerprint)
            _validate_inspection(
                inspection,
                configuration=configuration,
                image_id=state.image_id,
                labels=_labels_from_state(configuration, state),
                allocation=allocation,
                require_route=inspection.status == "running",
            )
            if inspection.status == "running":
                try:
                    await self._runtime.engine.stop_container(
                        state.container_id,
                        timeout_seconds=configuration.stop_grace_seconds,
                    )
                except DockerEngineError as error:
                    raise _unknown_failure("Docker stop outcome is unknown.") from error
            try:
                await self._runtime.engine.remove_container(state.container_id)
            except DockerEngineError as error:
                raise _unknown_failure("Docker remove outcome is unknown.") from error
            remaining = await _engine_call(self._runtime.engine.inspect_container(state.container_id))
            if remaining is not None:
                raise _unknown_failure("Docker target absence could not be confirmed.")
        else:
            matches = await _engine_call(self._runtime.engine.find_containers(_labels_from_state(configuration, state)))
            if matches:
                raise _unknown_failure("Docker target identity is ambiguous during destroy.")
        await _remove_bootstrap(self._runtime, state.bootstrap_correlation)


def _compose_operations(
    session: EIPSession,
    *,
    environment_id: str,
    mount_id: str,
    descriptor: EnvironmentDescriptor,
) -> tuple[EnvironmentOperations, EIPOutputRegistry, _ProcessConversions]:
    generation = descriptor.generation
    methods = set(session.descriptor.available_methods)
    files = EIPFileOperator(
        session=session,
        environment_id=environment_id,
        mount_id=mount_id,
        generation=generation,
    )
    outputs = EIPOutputRegistry(
        session=session,
        environment_id=environment_id,
        mount_id=mount_id,
        generation=generation,
    )
    conversions = _ProcessConversions(
        session=session,
        files=files,
        outputs=outputs,
        provider_type=_PROVIDER_KEY,
        environment_id=environment_id,
        mount_id=mount_id,
        generation=generation,
    )
    return (
        EnvironmentOperations(
            files=files if any(method.startswith("file.") for method in methods) else None,
            shell=EIPShellOperations(conversions) if "shell.exec" in methods else None,
            processes=EIPProcessOperations(conversions) if "process.start" in methods else None,
            ports=EIPPortOperations(conversions) if {"port.inspect", "port.wait"} & methods else None,
            outputs=EIPOutputOperations(outputs) if "output.read" in methods else None,
        ),
        outputs,
        conversions,
    )


def _convert_descriptor(descriptor: eip.EnvironmentDescriptor) -> EnvironmentDescriptor:
    methods = set(descriptor.available_methods)
    actions = {action for method in methods for action in _METHOD_ACTIONS.get(method, ())}
    if "process.inspect" in methods and "output.read" in methods:
        actions.add(EnvironmentAction.PROCESS_READ_OUTPUT)
    families: set[EnvironmentOperationFamily] = set()
    if any(method.startswith("file.") for method in methods):
        families.add("files")
    if "shell.exec" in methods:
        families.add("shell")
    if any(method.startswith("process.") for method in methods):
        families.add("processes")
    if any(method.startswith("port.") for method in methods):
        families.add("ports")
    if any(method.startswith("output.") for method in methods):
        families.add("outputs")
    limits = descriptor.limits
    return EnvironmentDescriptor(
        generation=str(descriptor.generation),
        operation_families=frozenset(families),
        permissions=EnvironmentPermissionSet(operations=frozenset(actions)),
        limits={
            "max_request_bytes": limits.max_request_bytes,
            "max_response_bytes": limits.max_response_bytes,
            "max_concurrent_operations": limits.max_concurrent_operations,
            "max_processes": limits.max_processes,
            "max_operation_duration_ms": limits.max_operation_duration_ms,
            "max_output_preview_bytes": limits.max_output_preview_bytes,
            "max_output_bytes_per_stream": limits.max_output_bytes_per_stream,
            "max_transfer_frame_bytes": limits.max_transfer_frame_bytes,
            "max_concurrent_file_transfers": limits.max_concurrent_file_transfers,
            "max_file_transfer_bytes": limits.max_file_transfer_bytes,
        },
        mounts=tuple(
            EnvironmentMountDescriptor(name=mount.mount_id, path=mount.logical_root, read_only=not mount.writable)
            for mount in descriptor.mounts
        ),
    )


def decode_state(
    state: EnvironmentState | None,
    configuration: DockerProviderConfiguration,
) -> DockerProviderStateData | None:
    if state is None:
        return None
    if state.provider_key != _PROVIDER_KEY or state.state_version != _STATE_VERSION:
        raise _state_failure("Docker state envelope is incompatible.")
    try:
        data = DockerProviderStateData.model_validate(state.state)
    except ValidationError as error:
        raise _state_failure("Docker state payload is invalid.") from error
    if (
        data.environment_id != configuration.environment_id
        or data.configuration_fingerprint != _configuration_fingerprint(configuration)
    ):
        raise _conflict_failure("Docker state is incompatible with the desired configuration.")
    return data


async def _canonical_configuration(configuration: DockerProviderConfiguration) -> DockerProviderConfiguration:
    def canonicalize() -> DockerProviderConfiguration:
        mounts: list[DockerMountConfiguration] = []
        for mount in configuration.mounts:
            source = mount.source
            if isinstance(source, DockerBindMountSource):
                try:
                    path = source.path.resolve(strict=True)
                except (OSError, ValueError) as error:
                    raise _state_failure("Docker bind source could not be resolved.") from error
                if not path.is_dir():
                    raise _state_failure("Docker bind source must be an existing directory.")
                source = source.model_copy(update={"path": path})
            mounts.append(mount.model_copy(update={"source": source}))
        return configuration.model_copy(update={"mounts": tuple(mounts)})

    return await asyncio.to_thread(canonicalize)


async def _resolve_image(
    runtime: DockerProviderRuntime,
    configuration: DockerProviderConfiguration,
) -> DockerImageInspection:
    try:
        image: DockerImageInspection | None = None
        if configuration.pull_policy is DockerImagePullPolicy.ALWAYS:
            await runtime.engine.pull_image(configuration.image)
        else:
            image = await runtime.engine.inspect_image(configuration.image)
            if image is None and configuration.pull_policy is DockerImagePullPolicy.IF_MISSING:
                await runtime.engine.pull_image(configuration.image)
        if image is None:
            image = await runtime.engine.inspect_image(configuration.image)
    except DockerEngineError as error:
        raise _runtime_failure("Docker image resolution failed.") from error
    if image is None:
        raise _missing_failure("Docker image is unavailable under the selected pull policy.")
    return image


def _validate_image(image: DockerImageInspection) -> None:
    if not image.user or image.user.strip().lower() in {"0", "root", "0:0", "root:root"}:
        raise _state_failure("Docker image must declare a fixed non-root user.")


def _configuration_fingerprint(configuration: DockerProviderConfiguration) -> str:
    payload = json.dumps(
        configuration.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _correlation(prefix: str, *values: str) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(value.encode())
        digest.update(b"\0")
    return f"{prefix}-{digest.hexdigest()[:24]}"


def _new_bootstrap_material(
    configuration: DockerProviderConfiguration,
    fingerprint: str,
) -> DockerBootstrapMaterial:
    return DockerBootstrapMaterial(
        environment_id=configuration.environment_id,
        configuration_fingerprint=fingerprint,
        envd_configuration=_envd_configuration(configuration),
        credential=secrets.token_urlsafe(32),
    )


def _envd_configuration(configuration: DockerProviderConfiguration) -> bytes:
    mounts = [
        {
            "mount_id": mount.mount_id,
            "native_root": str(mount.container_path),
            "writable": not mount.read_only,
            "allow_command_execution": mount.allow_command_execution,
            "max_file_bytes": configuration.max_file_bytes,
        }
        for mount in configuration.mounts
    ]
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
    value = {
        "mounts": mounts,
        "root_mount_id": configuration.root_mount_id,
        "trusted_executable_roots": [str(path) for path in configuration.trusted_executable_roots],
        "shell_profiles": shell_profiles,
        "limits": {
            "max_output_preview_bytes": configuration.max_output_preview_bytes,
            "max_output_bytes_per_stream": configuration.max_output_bytes_per_stream,
            "max_spool_bytes": configuration.max_spool_bytes,
        },
    }
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _configured_engine_mounts(configuration: DockerProviderConfiguration) -> tuple[DockerEngineMount, ...]:
    mounts: list[DockerEngineMount] = []
    for mount in configuration.mounts:
        source = mount.source
        if isinstance(source, DockerBindMountSource):
            mounts.append(
                DockerEngineMount(
                    type="bind",
                    source=str(source.path),
                    target=str(mount.container_path),
                    read_only=mount.read_only,
                )
            )
        elif isinstance(source, DockerVolumeMountSource):
            mounts.append(
                DockerEngineMount(
                    type="volume",
                    source=source.name,
                    target=str(mount.container_path),
                    read_only=mount.read_only,
                )
            )
    return tuple(mounts)


def _container_spec(
    configuration: DockerProviderConfiguration,
    *,
    image_id: str,
    labels: Mapping[str, str],
    mounts: tuple[DockerEngineMount, ...],
) -> DockerContainerSpec:
    environment = dict(_REQUIRED_ENVIRONMENT)
    environment["AGENT_ENVD_ENVIRONMENT_ID"] = configuration.environment_id
    return DockerContainerSpec(
        image_id=image_id,
        command=_COMMAND,
        environment=environment,
        labels=labels,
        mounts=mounts,
        eip_container_port=_EIP_CONTAINER_PORT,
        nano_cpus=configuration.nano_cpus,
        memory_bytes=configuration.memory_bytes,
        pids_limit=configuration.pids_limit,
    )


def _labels(
    configuration: DockerProviderConfiguration,
    *,
    bootstrap_correlation: str,
    configuration_fingerprint: str,
    create_correlation: str,
) -> dict[str, str]:
    return {
        _LABEL_PROVIDER: _PROVIDER_KEY,
        _LABEL_STATE: _STATE_VERSION,
        _LABEL_ENVIRONMENT: configuration.environment_id,
        _LABEL_BOOTSTRAP: bootstrap_correlation,
        _LABEL_FINGERPRINT: configuration_fingerprint,
        _LABEL_CREATE: create_correlation,
    }


def _labels_from_state(
    configuration: DockerProviderConfiguration,
    state: DockerProviderStateData,
) -> dict[str, str]:
    return _labels(
        configuration,
        bootstrap_correlation=state.bootstrap_correlation,
        configuration_fingerprint=state.configuration_fingerprint,
        create_correlation=state.create_correlation,
    )


def _state_data(
    configuration: DockerProviderConfiguration,
    *,
    container_id: str,
    image_id: str,
    bootstrap_correlation: str,
    configuration_fingerprint: str,
    create_correlation: str,
) -> DockerProviderStateData:
    return DockerProviderStateData(
        environment_id=configuration.environment_id,
        container_id=container_id,
        image_id=image_id,
        bootstrap_correlation=bootstrap_correlation,
        configuration_fingerprint=configuration_fingerprint,
        create_correlation=create_correlation,
    )


def _validate_bootstrap(
    allocation: DockerBootstrapAllocation,
    configuration: DockerProviderConfiguration,
    fingerprint: str,
) -> None:
    material = allocation.material
    if (
        material.environment_id != configuration.environment_id
        or material.configuration_fingerprint != fingerprint
        or material.envd_configuration != _envd_configuration(configuration)
        or not material.credential
    ):
        raise _conflict_failure("Docker bootstrap allocation is incompatible.")


def _validate_inspection(
    inspection: DockerContainerInspection,
    *,
    configuration: DockerProviderConfiguration,
    image_id: str,
    labels: Mapping[str, str],
    allocation: DockerBootstrapAllocation,
    require_route: bool,
) -> None:
    provider_labels = {key: value for key, value in inspection.labels.items() if key.startswith("io.a13n.")}
    if inspection.image_id != image_id or provider_labels != dict(labels):
        raise _conflict_failure("Docker container identity or labels are incompatible.")
    if inspection.command != _COMMAND:
        raise _conflict_failure("Docker container command is incompatible.")
    if not inspection.user or inspection.user.strip().lower() in {"0", "root", "0:0", "root:root"}:
        raise _conflict_failure("Docker container user is incompatible.")
    required_environment = dict(_REQUIRED_ENVIRONMENT)
    required_environment["AGENT_ENVD_ENVIRONMENT_ID"] = configuration.environment_id
    if any(inspection.environment.get(name) != value for name, value in required_environment.items()):
        raise _conflict_failure("Docker container environment is incompatible.")
    expected_mounts = {
        (mount.type, mount.source, mount.target, mount.read_only) for mount in _configured_engine_mounts(configuration)
    }
    expected_mounts.add(("bind", str(allocation.directory), _BOOTSTRAP_CONTAINER_PATH, True))
    actual_mounts = {(mount.type, mount.source, mount.target, mount.read_only) for mount in inspection.mounts}
    if actual_mounts != expected_mounts:
        raise _conflict_failure("Docker container mounts are incompatible.")
    if (
        inspection.nano_cpus != configuration.nano_cpus
        or inspection.memory_bytes != configuration.memory_bytes
        or inspection.pids_limit != configuration.pids_limit
    ):
        raise _conflict_failure("Docker container limits are incompatible.")
    if not inspection.eip_binding_exact or inspection.eip_host_ip != "127.0.0.1":
        raise _conflict_failure("Docker EIP publication is not exactly Host-loopback-only.")
    if require_route and (not inspection.eip_route_exact or inspection.eip_host_port is None):
        raise _conflict_failure("Docker container has no exact active EIP route.")


async def _engine_call(awaitable: Any) -> Any:
    try:
        return await awaitable
    except DockerEngineError as error:
        raise _runtime_failure("Docker Engine evidence is unavailable.") from error


async def _store_create(
    runtime: DockerProviderRuntime,
    correlation: str,
    material: DockerBootstrapMaterial,
) -> DockerBootstrapAllocation:
    try:
        return await runtime.bootstrap_store.create(correlation, material)
    except DockerBootstrapStoreError as error:
        raise _runtime_failure("Docker bootstrap allocation failed.") from error


async def _store_recover(
    runtime: DockerProviderRuntime,
    correlation: str,
) -> DockerBootstrapAllocation | None:
    try:
        return await runtime.bootstrap_store.recover(correlation)
    except DockerBootstrapStoreError as error:
        raise _runtime_failure("Docker bootstrap evidence is unavailable.") from error


async def _store_replace(
    runtime: DockerProviderRuntime,
    correlation: str,
    material: DockerBootstrapMaterial,
) -> DockerBootstrapAllocation:
    try:
        return await runtime.bootstrap_store.replace(correlation, material)
    except DockerBootstrapStoreError as error:
        raise _runtime_failure("Docker bootstrap credential replacement failed.") from error


async def _remove_bootstrap(runtime: DockerProviderRuntime, correlation: str) -> None:
    try:
        await runtime.bootstrap_store.remove(correlation)
    except DockerBootstrapStoreError as error:
        raise _cleanup_failure("Docker bootstrap cleanup failed.") from error
