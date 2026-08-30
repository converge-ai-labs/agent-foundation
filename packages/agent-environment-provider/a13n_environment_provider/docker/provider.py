from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager

from pydantic import BaseModel, ValidationError

from ..attachments import EIPEnvironmentAttachment, EnvironmentRuntimeAttachment, HttpEIPSessionSource
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
    DockerBindMountSource,
    DockerImagePullPolicy,
    DockerMountConfiguration,
    DockerProviderConfiguration,
    DockerProviderStateData,
    DockerResourcePhase,
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
_READ_OPERATIONS = ("stat", "read_text", "open_reader", "list", "find", "search")
_WRITE_OPERATIONS = ("write_text", "open_writer", "remove", "move")
_LABEL_PROVIDER = "io.a13n.environment-provider"
_LABEL_STATE = "io.a13n.environment-provider.state"
_LABEL_ENVIRONMENT = "io.a13n.environment-id"
_LABEL_RESOURCE = "io.a13n.resource-correlation"
_LABEL_BOOTSTRAP = "io.a13n.bootstrap-correlation"
_LABEL_FINGERPRINT = "io.a13n.configuration-fingerprint"
_LABEL_CREATE_OPERATION = "io.a13n.create-operation-id"
_CAPABILITIES = EnvironmentLifecycleCapabilities(
    pause_modes=frozenset({EnvironmentPauseMode.FILESYSTEM}),
    resource_allocation=EnvironmentResourceAllocation.MULTIPLE_FROM_SPEC,
    attachment_concurrency=EnvironmentAttachmentConcurrency.SINGLE,
)


class DockerEnvironmentProviderFactory(EnvironmentProviderFactory):
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
                f"Docker does not support schema version {schema_version!r}.",
                code="provider_schema_unsupported",
                category=EnvironmentProviderErrorCategory.UNSUPPORTED,
                certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
                context=EnvironmentProviderErrorContext(
                    provider_key=_PROVIDER_KEY,
                    schema_version=schema_version,
                ),
            )
        return DockerProviderConfiguration

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
        if not isinstance(runtime, DockerProviderRuntime):
            raise EnvironmentProviderError(
                "Docker requires DockerProviderRuntime.",
                code="provider_runtime_invalid",
                category=EnvironmentProviderErrorCategory.INVALID,
                certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                recovery_hint=EnvironmentProviderRecoveryHint.REFRESH_RUNTIME,
                context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
            )
        return DockerEnvironmentProvider(actual_configuration, runtime)


class DockerEnvironmentProvider(EnvironmentProvider):
    def __init__(self, configuration: DockerProviderConfiguration, runtime: DockerProviderRuntime) -> None:
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
        await _engine_read(self._runtime.engine.validate_local_topology())
        image = await _resolve_image(self._runtime, configuration)
        _validate_image(image)
        configured_mounts = _configured_engine_mounts(configuration)
        for mount in configured_mounts:
            await _engine_read(self._runtime.engine.validate_mount(mount))

        fingerprint = _configuration_fingerprint(configuration)
        bootstrap_correlation = _bootstrap_correlation(operation.operation_id)
        allocation: DockerBootstrapAllocation | None = None
        material: DockerBootstrapMaterial | None = None
        created_allocation = False
        container_dispatched = False
        try:
            allocation = await _store_recover(self._runtime, bootstrap_correlation)
            if allocation is None:
                material = _new_bootstrap_material(configuration, fingerprint)
                allocation = await _store_create(self._runtime, bootstrap_correlation, material)
                created_allocation = True
            else:
                _validate_bootstrap(allocation, configuration, fingerprint)
            bootstrap_mount = DockerEngineMount(
                type="bind",
                source=str(allocation.directory),
                target=_BOOTSTRAP_CONTAINER_PATH,
                read_only=True,
            )
            await _engine_read(self._runtime.engine.validate_mount(bootstrap_mount))
            labels = _labels(
                configuration,
                resource_correlation=operation.resource_correlation,
                bootstrap_correlation=bootstrap_correlation,
                configuration_fingerprint=fingerprint,
                create_operation_id=operation.operation_id,
            )
            spec = _container_spec(
                configuration,
                image_id=image.image_id,
                labels=labels,
                mounts=(*configured_mounts, bootstrap_mount),
            )
            container_dispatched = True
            container_id = await self._runtime.engine.create_container(spec)
            await self._runtime.engine.start_container(container_id)
            inspection = await _engine_read(self._runtime.engine.inspect_container(container_id))
            if inspection is None:
                raise _unknown_error(operation, "Docker container disappeared after start.")
            _validate_inspection(
                inspection,
                configuration=configuration,
                image_id=image.image_id,
                labels=labels,
                allocation=allocation,
                require_route=True,
            )
            if inspection.status != "running":
                raise _unknown_error(operation, "Docker container did not reach running state.")
        except EnvironmentProviderError as error:
            if created_allocation and not container_dispatched:
                await _remove_failed_bootstrap(self._runtime, bootstrap_correlation)
            if container_dispatched and error.certainty is not EnvironmentProviderOutcomeCertainty.UNKNOWN:
                raise _unknown_error(operation, error.description) from error
            raise
        except DockerEngineError as error:
            if created_allocation and not container_dispatched:
                await _remove_failed_bootstrap(self._runtime, bootstrap_correlation)
            if container_dispatched or error.dispatched:
                raise _unknown_error(operation, str(error)) from error
            raise _runtime_failure(str(error)) from error
        except asyncio.CancelledError as cancellation:
            if not container_dispatched and (created_allocation or material is not None):
                await _cleanup_cancelled_create(
                    self._runtime,
                    bootstrap_correlation,
                    material=material,
                    cancellation=cancellation,
                )
            raise
        except BaseException as error:
            if created_allocation and not container_dispatched:
                await _remove_failed_bootstrap(self._runtime, bootstrap_correlation)
            raise _runtime_failure("Docker create prerequisites failed.") from error

        state = _build_state(
            configuration,
            resource_correlation=operation.resource_correlation,
            container_id=inspection.container_id,
            image_id=inspection.image_id,
            bootstrap_correlation=bootstrap_correlation,
            create_operation_id=operation.operation_id,
            phase=DockerResourcePhase.RUNNING,
        )
        return DockerEnvironmentResource(
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
        state_data = _validate_state(
            state,
            configuration=configuration,
            resource_correlation=operation.resource_correlation,
        )
        await _engine_read(self._runtime.engine.validate_local_topology())
        inspection = await _engine_read(self._runtime.engine.inspect_container(state_data.container_id))
        if inspection is None:
            raise _missing_error("Docker container does not exist.", operation)
        allocation = await _store_recover(self._runtime, state_data.bootstrap_correlation)
        if allocation is None:
            raise _missing_error("Docker bootstrap allocation does not exist.", operation)
        labels = _labels_from_state(configuration, state_data)
        _validate_inspection(
            inspection,
            configuration=configuration,
            image_id=state_data.image_id,
            labels=labels,
            allocation=allocation,
            require_route=inspection.status == "running",
        )
        _validate_bootstrap(allocation, configuration, state_data.configuration_fingerprint)
        if inspection.status in {"created", "exited"}:
            replacement = _new_bootstrap_material(configuration, state_data.configuration_fingerprint)
            allocation = await _store_replace(self._runtime, state_data.bootstrap_correlation, replacement)
            try:
                await self._runtime.engine.start_container(state_data.container_id)
                inspection = await _engine_read(self._runtime.engine.inspect_container(state_data.container_id))
                if inspection is None:
                    raise _unknown_error(operation, "Docker container disappeared after resume.")
                _validate_inspection(
                    inspection,
                    configuration=configuration,
                    image_id=state_data.image_id,
                    labels=labels,
                    allocation=allocation,
                    require_route=True,
                )
                if inspection.status != "running":
                    raise _unknown_error(operation, "Docker container did not reach running state after resume.")
            except DockerEngineError as error:
                raise _unknown_error(operation, str(error)) from error
            except EnvironmentProviderError as error:
                if error.certainty is EnvironmentProviderOutcomeCertainty.UNKNOWN:
                    raise
                raise _unknown_error(operation, error.description) from error
        if inspection.status != "running":
            raise _state_error("Docker container is not resumable from its current state.", operation)
        running = _state_with_phase(state_data, DockerResourcePhase.RUNNING)
        return DockerEnvironmentResource(
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
                "Docker supports only filesystem pause.",
                code="provider_action_unsupported",
                category=EnvironmentProviderErrorCategory.UNSUPPORTED,
                certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                context=_operation_context(operation),
            )
        if (
            not isinstance(environment, DockerEnvironmentResource)
            or environment._provider_identity is not self._identity
        ):
            raise _state_error("Docker pause requires a Resource created by this Provider instance.", operation)
        environment._require_entered()
        state_data = _validate_state(
            environment.state,
            configuration=environment._configuration,
            resource_correlation=operation.resource_correlation,
        )
        inspection, allocation = await _inspect_owned_resource(
            self._runtime,
            environment._configuration,
            state_data,
            operation=operation,
        )
        await environment._close_admission_for_pause()
        if inspection.status == "running":
            try:
                await self._runtime.engine.stop_container(
                    state_data.container_id,
                    timeout_seconds=environment._configuration.stop_grace_seconds,
                )
                inspection = await _engine_read(self._runtime.engine.inspect_container(state_data.container_id))
                if inspection is None:
                    raise _unknown_error(operation, "Docker container disappeared while pausing.")
                _validate_inspection(
                    inspection,
                    configuration=environment._configuration,
                    image_id=state_data.image_id,
                    labels=_labels_from_state(environment._configuration, state_data),
                    allocation=allocation,
                    require_route=False,
                )
            except DockerEngineError as error:
                raise _unknown_error(operation, str(error)) from error
            except EnvironmentProviderError as error:
                if error.certainty is EnvironmentProviderOutcomeCertainty.UNKNOWN:
                    raise
                raise _unknown_error(operation, error.description) from error
        if inspection.status not in {"created", "exited"}:
            raise _unknown_error(operation, "Docker container stop could not be confirmed.")
        paused = _state_with_phase(state_data, DockerResourcePhase.PAUSED)
        environment._state = paused
        return paused

    async def destroy(
        self,
        state: EnvironmentProviderResourceState,
        *,
        operation: EnvironmentOperationContext,
    ) -> None:
        self._require_operation(operation, EnvironmentManagementAction.DESTROY, provider_key=_PROVIDER_KEY)
        configuration = await _validated_configuration(self._configuration)
        state_data = _validate_state(
            state,
            configuration=configuration,
            resource_correlation=operation.resource_correlation,
        )
        await _engine_read(self._runtime.engine.validate_local_topology())
        inspection = await _engine_read(self._runtime.engine.inspect_container(state_data.container_id))
        allocation = await _store_recover(self._runtime, state_data.bootstrap_correlation)
        if inspection is not None:
            if allocation is None:
                raise _unknown_error(operation, "Docker bootstrap evidence is missing while the container exists.")
            _validate_bootstrap(allocation, configuration, state_data.configuration_fingerprint)
            _validate_inspection(
                inspection,
                configuration=configuration,
                image_id=state_data.image_id,
                labels=_labels_from_state(configuration, state_data),
                allocation=allocation,
                require_route=inspection.status == "running",
            )
            mutation_dispatched = False
            try:
                if inspection.status == "running":
                    mutation_dispatched = True
                    await self._runtime.engine.stop_container(
                        state_data.container_id,
                        timeout_seconds=configuration.stop_grace_seconds,
                    )
                elif inspection.status not in {"created", "exited"}:
                    raise _unknown_error(operation, "Docker container state is not safe to destroy.")
                mutation_dispatched = True
                await self._runtime.engine.remove_container(state_data.container_id)
                remaining = await _engine_read(self._runtime.engine.inspect_container(state_data.container_id))
                if remaining is not None:
                    raise _unknown_error(operation, "Docker container removal could not be confirmed.")
            except DockerEngineError as error:
                raise _unknown_error(operation, str(error)) from error
            except EnvironmentProviderError as error:
                if mutation_dispatched and error.certainty is not EnvironmentProviderOutcomeCertainty.UNKNOWN:
                    raise _unknown_error(operation, error.description) from error
                raise
        try:
            await self._runtime.bootstrap_store.remove(state_data.bootstrap_correlation)
        except DockerBootstrapStoreError as error:
            raise EnvironmentProviderError(
                "Docker container is absent but bootstrap cleanup failed.",
                code="provider_cleanup_failed",
                category=EnvironmentProviderErrorCategory.CLEANUP,
                certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
                context=_operation_context(operation),
            ) from error

    async def reconcile(
        self,
        operation: EnvironmentOperationContext,
        *,
        last_known_state: EnvironmentProviderResourceState | None,
    ) -> EnvironmentReconciliationResult:
        self._require_reconciliation_operation(operation, provider_key=_PROVIDER_KEY)
        try:
            configuration = await _validated_configuration(self._configuration)
            await _engine_read(self._runtime.engine.validate_local_topology())
            if operation.action is EnvironmentManagementAction.CREATE and last_known_state is None:
                return await self._reconcile_create(configuration, operation)
            if last_known_state is None:
                return _unknown_reconciliation(operation, "last_known_state_missing")
            state_data = _validate_state(
                last_known_state,
                configuration=configuration,
                resource_correlation=operation.resource_correlation,
            )
            inspection = await _engine_read(self._runtime.engine.inspect_container(state_data.container_id))
            allocation = await _store_recover(self._runtime, state_data.bootstrap_correlation)
            if inspection is None:
                if allocation is None:
                    return EnvironmentReconciliationResult(
                        operation_id=operation.operation_id,
                        phase=EnvironmentReconciliationPhase.ABSENT,
                        evidence={"container": "absent", "bootstrap": "absent"},
                    )
                return _unknown_reconciliation(operation, "container_absent_bootstrap_present")
            if allocation is None:
                return _unknown_reconciliation(operation, "container_present_bootstrap_absent")
            _validate_bootstrap(allocation, configuration, state_data.configuration_fingerprint)
            _validate_inspection(
                inspection,
                configuration=configuration,
                image_id=state_data.image_id,
                labels=_labels_from_state(configuration, state_data),
                allocation=allocation,
                require_route=inspection.status == "running",
            )
            return _inspection_reconciliation(operation, state_data, inspection)
        except EnvironmentProviderError as error:
            if error.certainty is EnvironmentProviderOutcomeCertainty.UNKNOWN:
                raise
            return _unknown_reconciliation(operation, error.code)
        except (DockerEngineError, DockerBootstrapStoreError, OSError, ValueError):
            return _unknown_reconciliation(operation, "provider_evidence_unavailable")

    async def _reconcile_create(
        self,
        configuration: DockerProviderConfiguration,
        operation: EnvironmentOperationContext,
    ) -> EnvironmentReconciliationResult:
        fingerprint = _configuration_fingerprint(configuration)
        bootstrap_correlation = _bootstrap_correlation(operation.operation_id)
        labels = _labels(
            configuration,
            resource_correlation=operation.resource_correlation,
            bootstrap_correlation=bootstrap_correlation,
            configuration_fingerprint=fingerprint,
            create_operation_id=operation.operation_id,
        )
        inspections = await _engine_read(self._runtime.engine.find_containers(labels))
        allocation = await _store_recover(self._runtime, bootstrap_correlation)
        if not inspections:
            if allocation is not None:
                _validate_bootstrap(allocation, configuration, fingerprint)
            return EnvironmentReconciliationResult(
                operation_id=operation.operation_id,
                phase=EnvironmentReconciliationPhase.ABSENT,
                evidence={"matching_container_count": 0},
            )
        if len(inspections) != 1 or allocation is None:
            return _unknown_reconciliation(operation, "create_evidence_ambiguous")
        inspection = inspections[0]
        _validate_bootstrap(allocation, configuration, fingerprint)
        _validate_inspection(
            inspection,
            configuration=configuration,
            image_id=inspection.image_id,
            labels=labels,
            allocation=allocation,
            require_route=inspection.status == "running",
        )
        state = _build_state(
            configuration,
            resource_correlation=operation.resource_correlation,
            container_id=inspection.container_id,
            image_id=inspection.image_id,
            bootstrap_correlation=bootstrap_correlation,
            create_operation_id=operation.operation_id,
            phase=DockerResourcePhase.RUNNING if inspection.status == "running" else DockerResourcePhase.PAUSED,
        )
        phase = (
            EnvironmentReconciliationPhase.RUNNING
            if inspection.status == "running"
            else EnvironmentReconciliationPhase.PAUSED
        )
        if inspection.status not in {"running", "created", "exited"}:
            return _unknown_reconciliation(operation, "container_state_ambiguous")
        return EnvironmentReconciliationResult(
            operation_id=operation.operation_id,
            phase=phase,
            state=state,
            evidence={"matching_container_count": 1, "container_status": inspection.status},
        )


class DockerEnvironmentResource(EnvironmentResource):
    def __init__(
        self,
        configuration: DockerProviderConfiguration,
        state: EnvironmentProviderResourceState,
        *,
        runtime: DockerProviderRuntime,
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
        self._endpoint: str | None = None
        self._credential: str | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> EnvironmentProviderResourceState:
        return self._state

    async def _enter_scope(self) -> None:
        state_data = _validate_state(
            self._state,
            configuration=self._configuration,
            resource_correlation=DockerProviderStateData.model_validate(self._state.data).resource_correlation,
        )
        try:
            inspection, allocation = await _inspect_owned_resource(
                self._runtime,
                self._configuration,
                state_data,
            )
            if inspection.status != "running":
                raise _runtime_failure("Docker Resource container is not running.")
            if inspection.eip_host_ip != "127.0.0.1" or inspection.eip_host_port is None:
                raise _runtime_failure("Docker Resource has no authoritative Host-loopback EIP route.")
            endpoint = f"http://127.0.0.1:{inspection.eip_host_port}"
            source = _http_source(endpoint, allocation.material.credential)
            try:
                async with source.open_session(
                    expected_environment_id=self._configuration.environment_id,
                    required_methods=frozenset({"environment.readiness", "session.close"}),
                ):
                    pass
            finally:
                await source.discard()
            async with self._lock:
                self._endpoint = endpoint
                self._credential = allocation.material.credential
                self._admitting = True
        except asyncio.CancelledError:
            raise
        except EnvironmentProviderError:
            raise
        except Exception as error:
            raise _runtime_failure("Docker Resource EIP readiness failed.") from error

    @asynccontextmanager
    async def acquire_attachment(self) -> AsyncGenerator[EnvironmentRuntimeAttachment]:
        self._require_entered()
        async with self._lock:
            if self._active_attachments:
                raise _attachment_conflict("Docker Resource already has an active attachment.")
            if not self._admitting or self._endpoint is None or self._credential is None:
                raise _attachment_conflict("Docker Resource attachment admission is closed.")
            self._attachment_sequence += 1
            self._active_attachments = 1
            source = _http_source(self._endpoint, self._credential)
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
            except asyncio.CancelledError as error:
                release_error = error
            except BaseException as error:
                release_error = _runtime_failure("Docker attachment release failed.")
                release_error.add_note(repr(error))
            async with self._lock:
                self._active_attachments = 0
            if isinstance(release_error, asyncio.CancelledError):
                if primary_error is not None:
                    release_error.add_note(f"Docker attachment use also failed: {primary_error!r}")
                raise release_error
            if release_error is not None:
                if isinstance(primary_error, asyncio.CancelledError):
                    primary_error.add_note(f"Docker attachment release also failed: {release_error!r}")
                elif primary_error is not None:
                    raise BaseExceptionGroup(
                        "Docker attachment use and release failed",
                        [primary_error, release_error],
                    ) from None
                else:
                    raise release_error

    async def _close_admission_for_pause(self) -> None:
        async with self._lock:
            if self._active_attachments:
                raise _attachment_conflict("Docker cannot pause with an active attachment scope.")
            self._admitting = False
            self._credential = None
            self._endpoint = None

    async def _exit_scope(self) -> None:
        async with self._lock:
            self._admitting = False
            self._credential = None
            self._endpoint = None
            active = self._active_attachments
        if active:
            raise EnvironmentProviderError(
                "Docker EnvironmentResource closed with active attachment scopes.",
                code="provider_cleanup_failed",
                category=EnvironmentProviderErrorCategory.CLEANUP,
                certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
                context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
                details={"active_attachment_count": active},
            )


async def _resolve_image(
    runtime: DockerProviderRuntime,
    configuration: DockerProviderConfiguration,
) -> DockerImageInspection:
    image: DockerImageInspection | None = None
    try:
        if configuration.pull_policy is DockerImagePullPolicy.ALWAYS:
            await runtime.engine.pull_image(configuration.image)
        else:
            image = await runtime.engine.inspect_image(configuration.image)
            if image is None and configuration.pull_policy is DockerImagePullPolicy.IF_MISSING:
                await runtime.engine.pull_image(configuration.image)
        if image is None:
            image = await runtime.engine.inspect_image(configuration.image)
    except DockerEngineError as error:
        raise _runtime_failure(str(error)) from error
    if image is None:
        raise EnvironmentProviderError(
            "Docker image is not available under the selected pull policy.",
            code="provider_resource_missing",
            category=EnvironmentProviderErrorCategory.MISSING,
            certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
            recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
            context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
        )
    return image


def _validate_image(image: DockerImageInspection) -> None:
    user = image.user.strip().lower()
    if not user or user in {"0", "root", "0:0", "root:root"}:
        raise _spec_error("Docker image must declare a fixed non-root user.")


async def _validated_configuration(
    configuration: DockerProviderConfiguration,
) -> DockerProviderConfiguration:
    return await asyncio.to_thread(_canonical_configuration, configuration)


def _canonical_configuration(configuration: DockerProviderConfiguration) -> DockerProviderConfiguration:
    mounts: list[DockerMountConfiguration] = []
    for mount in configuration.mounts:
        source = mount.source
        if isinstance(source, DockerBindMountSource):
            try:
                path = source.path.resolve(strict=True)
            except (OSError, ValueError) as error:
                raise _spec_error("Docker bind source could not be resolved.") from error
            if not path.is_dir():
                raise _spec_error("Docker bind source must be an existing directory.")
            source = source.model_copy(update={"path": path})
        mounts.append(mount.model_copy(update={"source": source}))
    return configuration.model_copy(update={"mounts": tuple(mounts)})


def _configuration_fingerprint(configuration: DockerProviderConfiguration) -> str:
    payload = json.dumps(
        configuration.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _bootstrap_correlation(create_operation_id: str) -> str:
    digest = hashlib.sha256(create_operation_id.encode()).hexdigest()
    return f"bootstrap-{digest[:24]}"


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
    mounts = []
    for mount in configuration.mounts:
        operations: list[str] = list(_READ_OPERATIONS)
        if not mount.read_only:
            operations.extend(_WRITE_OPERATIONS)
        if mount.allow_command_execution:
            operations.extend(("command_cwd", "executable_source"))
        mounts.append(
            {
                "mount_id": mount.mount_id,
                "native_root": str(mount.container_path),
                "writable": not mount.read_only,
                "allow_command_execution": mount.allow_command_execution,
                "max_file_bytes": configuration.max_file_bytes,
                "allowed_operations": operations,
            }
        )
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
        "root_mount_id": configuration.root_mount_id,
        "limits": {
            "max_output_preview_bytes": configuration.max_output_preview_bytes,
            "max_output_bytes_per_stream": configuration.max_output_bytes_per_stream,
            "max_spool_bytes": configuration.max_spool_bytes,
        },
        "mounts": mounts,
        "trusted_executable_roots": [str(path) for path in configuration.trusted_executable_roots],
        "shell_profiles": shell_profiles,
        "execution": {"isolation": "disabled", "network": "host", "extra_read_only_paths": []},
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


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
    resource_correlation: str,
    bootstrap_correlation: str,
    configuration_fingerprint: str,
    create_operation_id: str,
) -> dict[str, str]:
    return {
        _LABEL_PROVIDER: _PROVIDER_KEY,
        _LABEL_STATE: _STATE_VERSION,
        _LABEL_ENVIRONMENT: configuration.environment_id,
        _LABEL_RESOURCE: resource_correlation,
        _LABEL_BOOTSTRAP: bootstrap_correlation,
        _LABEL_FINGERPRINT: configuration_fingerprint,
        _LABEL_CREATE_OPERATION: create_operation_id,
    }


def _labels_from_state(
    configuration: DockerProviderConfiguration,
    state: DockerProviderStateData,
) -> dict[str, str]:
    return _labels(
        configuration,
        resource_correlation=state.resource_correlation,
        bootstrap_correlation=state.bootstrap_correlation,
        configuration_fingerprint=state.configuration_fingerprint,
        create_operation_id=state.create_operation_id,
    )


def _build_state(
    configuration: DockerProviderConfiguration,
    *,
    resource_correlation: str,
    container_id: str,
    image_id: str,
    bootstrap_correlation: str,
    create_operation_id: str,
    phase: DockerResourcePhase,
) -> EnvironmentProviderResourceState:
    data = DockerProviderStateData(
        environment_id=configuration.environment_id,
        resource_correlation=resource_correlation,
        container_id=container_id,
        image_id=image_id,
        bootstrap_correlation=bootstrap_correlation,
        configuration_fingerprint=_configuration_fingerprint(configuration),
        create_operation_id=create_operation_id,
        phase=phase,
    )
    return EnvironmentProviderResourceState(
        provider_key=_PROVIDER_KEY,
        state_version=_STATE_VERSION,
        data=data.model_dump(mode="json"),
    )


def _state_with_phase(
    data: DockerProviderStateData,
    phase: DockerResourcePhase,
) -> EnvironmentProviderResourceState:
    updated = data.model_copy(update={"phase": phase})
    return EnvironmentProviderResourceState(
        provider_key=_PROVIDER_KEY,
        state_version=_STATE_VERSION,
        data=updated.model_dump(mode="json"),
    )


def _validate_state(
    state: EnvironmentProviderResourceState,
    *,
    configuration: DockerProviderConfiguration,
    resource_correlation: str,
) -> DockerProviderStateData:
    if state.provider_key != _PROVIDER_KEY or state.state_version != _STATE_VERSION:
        raise _state_error("Docker state envelope is incompatible.")
    try:
        data = DockerProviderStateData.model_validate(state.data)
    except ValidationError as error:
        raise _state_error("Docker state data is invalid.") from error
    if (
        data.environment_id != configuration.environment_id
        or data.resource_correlation != resource_correlation
        or data.configuration_fingerprint != _configuration_fingerprint(configuration)
    ):
        raise _state_error("Docker state does not match this configuration and resource.")
    return data


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
        raise _state_error("Docker bootstrap allocation does not match this resource.")


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
        raise _state_error("Docker container identity or labels do not match this resource.")
    if inspection.command != _COMMAND:
        raise _state_error("Docker container command does not match the fixed envd command.")
    if not inspection.user or inspection.user.strip().lower() in {"0", "root", "0:0", "root:root"}:
        raise _state_error("Docker container is not running under a fixed non-root user.")
    required_environment = dict(_REQUIRED_ENVIRONMENT)
    required_environment["AGENT_ENVD_ENVIRONMENT_ID"] = configuration.environment_id
    if any(inspection.environment.get(name) != value for name, value in required_environment.items()):
        raise _state_error("Docker container envd environment does not match this resource.")
    expected_mounts = {
        (mount.type, mount.source, mount.target, mount.read_only) for mount in _configured_engine_mounts(configuration)
    }
    expected_mounts.add(("bind", str(allocation.directory), _BOOTSTRAP_CONTAINER_PATH, True))
    actual_mounts = {(mount.type, mount.source, mount.target, mount.read_only) for mount in inspection.mounts}
    if actual_mounts != expected_mounts:
        raise _state_error("Docker container mounts do not match this resource.")
    if (
        inspection.nano_cpus != configuration.nano_cpus
        or inspection.memory_bytes != configuration.memory_bytes
        or inspection.pids_limit != configuration.pids_limit
    ):
        raise _state_error("Docker container resource limits do not match this configuration.")
    if not inspection.eip_binding_exact or inspection.eip_host_ip != "127.0.0.1":
        raise _state_error("Docker container EIP port publication is not exactly Host-loopback-only.")
    if require_route and (not inspection.eip_route_exact or inspection.eip_host_port is None):
        raise _state_error("Docker container has no exact active Host-loopback EIP route.")


async def _inspect_owned_resource(
    runtime: DockerProviderRuntime,
    configuration: DockerProviderConfiguration,
    state: DockerProviderStateData,
    *,
    operation: EnvironmentOperationContext | None = None,
) -> tuple[DockerContainerInspection, DockerBootstrapAllocation]:
    inspection = await _engine_read(runtime.engine.inspect_container(state.container_id))
    if inspection is None:
        raise _missing_error("Docker container does not exist.", operation)
    allocation = await _store_recover(runtime, state.bootstrap_correlation)
    if allocation is None:
        raise _missing_error("Docker bootstrap allocation does not exist.", operation)
    _validate_bootstrap(allocation, configuration, state.configuration_fingerprint)
    _validate_inspection(
        inspection,
        configuration=configuration,
        image_id=state.image_id,
        labels=_labels_from_state(configuration, state),
        allocation=allocation,
        require_route=True,
    )
    return inspection, allocation


def _inspection_reconciliation(
    operation: EnvironmentOperationContext,
    state: DockerProviderStateData,
    inspection: DockerContainerInspection,
) -> EnvironmentReconciliationResult:
    if inspection.status == "running":
        phase = EnvironmentReconciliationPhase.RUNNING
        resource_phase = DockerResourcePhase.RUNNING
    elif inspection.status in {"created", "exited"}:
        phase = EnvironmentReconciliationPhase.PAUSED
        resource_phase = DockerResourcePhase.PAUSED
    else:
        return _unknown_reconciliation(operation, "container_state_ambiguous")
    return EnvironmentReconciliationResult(
        operation_id=operation.operation_id,
        phase=phase,
        state=_state_with_phase(state, resource_phase),
        evidence={"container_status": inspection.status, "bootstrap": "present"},
    )


async def _engine_read(awaitable):
    try:
        return await awaitable
    except DockerEngineError as error:
        raise _runtime_failure(str(error)) from error


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
        raise _runtime_failure("Docker bootstrap recovery failed.") from error


async def _store_replace(
    runtime: DockerProviderRuntime,
    correlation: str,
    material: DockerBootstrapMaterial,
) -> DockerBootstrapAllocation:
    try:
        return await runtime.bootstrap_store.replace(correlation, material)
    except DockerBootstrapStoreError as error:
        raise _runtime_failure("Docker bootstrap replacement failed.") from error


async def _cleanup_cancelled_create(
    runtime: DockerProviderRuntime,
    correlation: str,
    *,
    material: DockerBootstrapMaterial | None,
    cancellation: asyncio.CancelledError,
) -> None:
    async def cleanup() -> None:
        allocation = await _store_recover(runtime, correlation)
        if allocation is not None and (material is None or allocation.material.digest == material.digest):
            await _remove_failed_bootstrap(runtime, correlation)

    cleanup_task = asyncio.create_task(cleanup(), name="docker-cancelled-create-cleanup")
    while True:
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError as error:
            if cleanup_task.cancelled():
                cancellation.add_note(f"Docker cancelled-create cleanup was cancelled: {error!r}")
                return
            continue
        except BaseException as error:
            cancellation.add_note(f"Docker cancelled-create cleanup also failed: {error!r}")
        return


async def _remove_failed_bootstrap(runtime: DockerProviderRuntime, correlation: str) -> None:
    try:
        await runtime.bootstrap_store.remove(correlation)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        failure = EnvironmentProviderError(
            "Docker create failed before dispatch and bootstrap cleanup also failed.",
            code="provider_cleanup_failed",
            category=EnvironmentProviderErrorCategory.CLEANUP,
            certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
            context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
        )
        failure.add_note(repr(error))
        raise failure from error


def _http_source(endpoint: str, credential: str) -> HttpEIPSessionSource:
    return HttpEIPSessionSource(
        endpoint,
        credential,
        initialization_timeout=10.0,
        request_timeout=30.0,
        allow_plaintext_private_link=True,
    )


def _require_configuration(configuration: BaseModel) -> DockerProviderConfiguration:
    if not isinstance(configuration, DockerProviderConfiguration):
        raise _spec_error("Docker requires DockerProviderConfiguration.")
    return configuration


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
        else EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
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


def _missing_error(
    description: str,
    operation: EnvironmentOperationContext | None,
) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        description,
        code="provider_resource_missing",
        category=EnvironmentProviderErrorCategory.MISSING,
        certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
        context=_operation_context(operation)
        if operation is not None
        else EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
    )


def _attachment_conflict(description: str) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        description,
        code="provider_attachment_conflict",
        category=EnvironmentProviderErrorCategory.CONFLICT,
        certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
        context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
    )


def _unknown_error(operation: EnvironmentOperationContext, description: str) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        description,
        code="provider_unknown_outcome",
        category=EnvironmentProviderErrorCategory.UNKNOWN_OUTCOME,
        certainty=EnvironmentProviderOutcomeCertainty.UNKNOWN,
        recovery_hint=EnvironmentProviderRecoveryHint.RECONCILE,
        context=_operation_context(operation),
    )


def _unknown_reconciliation(
    operation: EnvironmentOperationContext,
    reason: str,
) -> EnvironmentReconciliationResult:
    return EnvironmentReconciliationResult(
        operation_id=operation.operation_id,
        phase=EnvironmentReconciliationPhase.UNKNOWN,
        evidence={"reason": reason},
    )
