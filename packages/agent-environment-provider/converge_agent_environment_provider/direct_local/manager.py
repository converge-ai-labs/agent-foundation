from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

from ..attachments import DirectLocalEnvironmentAttachment, EnvironmentRuntimeAttachment
from ..errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
)
from ..factories import EnvironmentProviderFactory
from ..management import EnvironmentManager, EnvironmentProviderRuntime, ManagedEnvironment
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
from .configuration import DirectLocalProviderConfiguration, DirectLocalProviderStateData

_PROVIDER_KEY = "converge.direct-local"
_STATE_VERSION = "1"
_CAPABILITIES = EnvironmentLifecycleCapabilities(
    pause_modes=frozenset(),
    resource_allocation=EnvironmentResourceAllocation.SINGLE_FROM_SPEC,
    attachment_concurrency=EnvironmentAttachmentConcurrency.SHARED,
)


@dataclass(frozen=True, slots=True)
class DirectLocalProviderRuntime(EnvironmentProviderRuntime):
    """Explicit empty runtime collaborator for Direct Local."""


class DirectLocalEnvironmentProviderFactory(EnvironmentProviderFactory):
    @classmethod
    def provider_key(cls) -> str:
        return _PROVIDER_KEY

    @classmethod
    def supported_schema_versions(cls) -> frozenset[str]:
        return frozenset({"1"})

    @classmethod
    def configuration_model(cls, schema_version: str) -> type[BaseModel]:
        if schema_version != "1":
            raise EnvironmentProviderError(
                f"Direct Local does not support schema version {schema_version!r}.",
                code="provider_schema_unsupported",
                category=EnvironmentProviderErrorCategory.UNSUPPORTED,
                certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
                context=EnvironmentProviderErrorContext(
                    provider_key=_PROVIDER_KEY,
                    schema_version=schema_version,
                ),
            )
        return DirectLocalProviderConfiguration

    def create_manager(
        self,
        configuration: BaseModel,
        *,
        runtime: EnvironmentProviderRuntime,
    ) -> EnvironmentManager:
        if not isinstance(configuration, DirectLocalProviderConfiguration):
            raise EnvironmentProviderError(
                "Direct Local requires DirectLocalProviderConfiguration.",
                code="provider_spec_invalid",
                category=EnvironmentProviderErrorCategory.INVALID,
                certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
                context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
            )
        if not isinstance(runtime, DirectLocalProviderRuntime):
            raise EnvironmentProviderError(
                "Direct Local requires DirectLocalProviderRuntime.",
                code="provider_runtime_invalid",
                category=EnvironmentProviderErrorCategory.INVALID,
                certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                recovery_hint=EnvironmentProviderRecoveryHint.REFRESH_RUNTIME,
                context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
            )
        return DirectLocalEnvironmentManager(configuration)


class DirectLocalEnvironmentManager(EnvironmentManager):
    def __init__(self, configuration: DirectLocalProviderConfiguration) -> None:
        super().__init__()
        self._configuration = configuration.model_copy(deep=True)

    @property
    def lifecycle_capabilities(self) -> EnvironmentLifecycleCapabilities:
        return _CAPABILITIES

    async def create(self, *, operation: EnvironmentOperationContext) -> ManagedEnvironment:
        self._require_operation(operation, EnvironmentManagementAction.CREATE, provider_key=_PROVIDER_KEY)
        configuration = await _validated_configuration(self._configuration)
        return DirectLocalManagedEnvironment(configuration, _build_state(configuration))

    async def resume(
        self,
        state: EnvironmentProviderResourceState,
        *,
        operation: EnvironmentOperationContext,
    ) -> ManagedEnvironment:
        self._require_operation(operation, EnvironmentManagementAction.RESUME, provider_key=_PROVIDER_KEY)
        configuration = await _validated_configuration(self._configuration)
        expected = _build_state(configuration)
        _validate_state(state, expected=expected)
        return DirectLocalManagedEnvironment(configuration, expected)

    async def pause(
        self,
        environment: ManagedEnvironment,
        *,
        operation: EnvironmentOperationContext,
        mode: EnvironmentPauseMode = EnvironmentPauseMode.FULL,
    ) -> EnvironmentProviderResourceState:
        del environment, mode
        self._require_operation(operation, EnvironmentManagementAction.PAUSE, provider_key=_PROVIDER_KEY)
        raise EnvironmentProviderError(
            "Direct Local does not support pause.",
            code="provider_action_unsupported",
            category=EnvironmentProviderErrorCategory.UNSUPPORTED,
            certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
            recovery_hint=EnvironmentProviderRecoveryHint.NONE,
            context=_operation_context(operation),
        )

    async def destroy(
        self,
        state: EnvironmentProviderResourceState,
        *,
        operation: EnvironmentOperationContext,
    ) -> None:
        self._require_operation(operation, EnvironmentManagementAction.DESTROY, provider_key=_PROVIDER_KEY)
        observation, canonical_root = await asyncio.to_thread(_inspect_root, self._configuration.root.path)
        if observation == "running":
            assert canonical_root is not None
            configuration = self._configuration.model_copy(
                update={"root": self._configuration.root.model_copy(update={"path": canonical_root})},
                deep=True,
            )
            _validate_state(state, expected=_build_state(configuration))
        else:
            _validate_state_shape(state, environment_id=self._configuration.environment_id)

    async def reconcile(
        self,
        operation: EnvironmentOperationContext,
        *,
        last_known_state: EnvironmentProviderResourceState | None,
    ) -> EnvironmentReconciliationResult:
        self._require_reconciliation_operation(operation, provider_key=_PROVIDER_KEY)
        if operation.action is EnvironmentManagementAction.PAUSE:
            raise EnvironmentProviderError(
                "Direct Local pause cannot be reconciled because it is unsupported and never dispatched.",
                code="provider_action_unsupported",
                category=EnvironmentProviderErrorCategory.UNSUPPORTED,
                certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                context=_operation_context(operation),
            )
        if operation.action is EnvironmentManagementAction.DESTROY:
            if last_known_state is not None:
                _validate_state_shape(
                    last_known_state,
                    environment_id=self._configuration.environment_id,
                )
            return EnvironmentReconciliationResult(
                operation_id=operation.operation_id,
                phase=EnvironmentReconciliationPhase.ABSENT,
                evidence={"logical_resource": "detached"},
            )
        observation, canonical_root = await asyncio.to_thread(_inspect_root, self._configuration.root.path)
        if observation == "running":
            assert canonical_root is not None
            configuration = self._configuration.model_copy(
                update={"root": self._configuration.root.model_copy(update={"path": canonical_root})},
                deep=True,
            )
            state = _build_state(configuration)
            if last_known_state is not None:
                _validate_state(last_known_state, expected=state)
            return EnvironmentReconciliationResult(
                operation_id=operation.operation_id,
                phase=EnvironmentReconciliationPhase.RUNNING,
                state=state,
                evidence={"root": "accessible_directory"},
            )
        if last_known_state is not None:
            _validate_state_shape(
                last_known_state,
                environment_id=self._configuration.environment_id,
            )
        if observation == "absent":
            return EnvironmentReconciliationResult(
                operation_id=operation.operation_id,
                phase=EnvironmentReconciliationPhase.ABSENT,
                evidence={"root": "absent"},
            )
        return EnvironmentReconciliationResult(
            operation_id=operation.operation_id,
            phase=EnvironmentReconciliationPhase.UNKNOWN,
            evidence={"root": "unavailable"},
        )


class DirectLocalManagedEnvironment(ManagedEnvironment):
    def __init__(
        self,
        configuration: DirectLocalProviderConfiguration,
        state: EnvironmentProviderResourceState,
    ) -> None:
        super().__init__()
        self._configuration = configuration
        self._state = state
        self._attachment_sequence = 0
        self._active_attachments = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> EnvironmentProviderResourceState:
        return self._state

    @asynccontextmanager
    async def acquire_attachment(self) -> AsyncGenerator[EnvironmentRuntimeAttachment]:
        self._require_entered()
        async with self._lock:
            self._attachment_sequence += 1
            self._active_attachments += 1
            attachment_id = f"attachment-{self._attachment_sequence}"
        attachment = DirectLocalEnvironmentAttachment(
            attachment_id=attachment_id,
            environment_id=self._configuration.environment_id,
            configuration=self._configuration,
        )
        try:
            yield attachment
        finally:
            async with self._lock:
                self._active_attachments -= 1

    async def _exit_scope(self) -> None:
        async with self._lock:
            active = self._active_attachments
        if active:
            raise EnvironmentProviderError(
                "Direct Local managed resource closed with active attachment scopes.",
                code="provider_cleanup_failed",
                category=EnvironmentProviderErrorCategory.CLEANUP,
                certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
                context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
                details={"active_attachment_count": active},
            )


def _build_state(configuration: DirectLocalProviderConfiguration) -> EnvironmentProviderResourceState:
    payload = configuration.model_dump(mode="json")
    payload["allowed_executables"] = sorted(payload["allowed_executables"])
    payload["allowed_environment_keys"] = sorted(payload["allowed_environment_keys"])
    payload["allowed_ports"] = sorted(payload["allowed_ports"])
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    data = DirectLocalProviderStateData(
        environment_id=configuration.environment_id,
        configuration_fingerprint=f"sha256:{hashlib.sha256(encoded).hexdigest()}",
    )
    return EnvironmentProviderResourceState(
        provider_key=_PROVIDER_KEY,
        state_version=_STATE_VERSION,
        data=data.model_dump(mode="json"),
    )


async def _validated_configuration(
    configuration: DirectLocalProviderConfiguration,
) -> DirectLocalProviderConfiguration:
    root = await asyncio.to_thread(_resolve_root, configuration.root.path)
    return configuration.model_copy(
        update={"root": configuration.root.model_copy(update={"path": root})},
        deep=True,
    )


def _resolve_root(path: Path) -> Path:
    try:
        root = path.resolve(strict=True)
        if not root.is_dir():
            raise EnvironmentProviderError(
                f"Direct Local root is not a directory: {root}",
                code="provider_spec_invalid",
                category=EnvironmentProviderErrorCategory.INVALID,
                certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
                context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
            )
        return root
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise EnvironmentProviderError(
            f"Direct Local root does not exist: {path}",
            code="provider_resource_missing",
            category=EnvironmentProviderErrorCategory.MISSING,
            certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
            recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
            context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
        ) from exc
    except PermissionError as exc:
        raise EnvironmentProviderError(
            f"Direct Local root is not accessible: {path}",
            code="provider_denied",
            category=EnvironmentProviderErrorCategory.DENIED,
            certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
            context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
        ) from exc
    except (OSError, ValueError) as exc:
        raise EnvironmentProviderError(
            f"Direct Local root could not be inspected: {path}",
            code="provider_unavailable",
            category=EnvironmentProviderErrorCategory.UNAVAILABLE,
            certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
            recovery_hint=EnvironmentProviderRecoveryHint.RETRY_SAME_OPERATION,
            context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
        ) from exc


def _inspect_root(path: Path) -> tuple[str, Path | None]:
    try:
        root = path.resolve(strict=True)
        return ("running", root) if root.is_dir() else ("absent", None)
    except (FileNotFoundError, NotADirectoryError):
        return "absent", None
    except (PermissionError, OSError, ValueError):
        return "unknown", None


def _validate_state_shape(
    state: EnvironmentProviderResourceState,
    *,
    environment_id: str | None = None,
) -> DirectLocalProviderStateData:
    context = EnvironmentProviderErrorContext(
        provider_key=_PROVIDER_KEY,
        state_version=state.state_version,
    )
    if state.provider_key != _PROVIDER_KEY or state.state_version != _STATE_VERSION:
        raise EnvironmentProviderError(
            "Direct Local provider state key or version is incompatible.",
            code="provider_state_invalid",
            category=EnvironmentProviderErrorCategory.INVALID,
            certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
            recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
            context=context,
        )
    try:
        parsed = DirectLocalProviderStateData.model_validate(state.data)
    except ValidationError as exc:
        raise EnvironmentProviderError(
            "Direct Local provider state payload is invalid.",
            code="provider_state_invalid",
            category=EnvironmentProviderErrorCategory.INVALID,
            certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
            recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
            context=context,
        ) from exc
    if environment_id is not None and parsed.environment_id != environment_id:
        raise EnvironmentProviderError(
            "Direct Local provider state environment identity is incompatible.",
            code="provider_state_invalid",
            category=EnvironmentProviderErrorCategory.INVALID,
            certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
            recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
            context=context,
        )
    return parsed


def _validate_state(
    state: EnvironmentProviderResourceState,
    *,
    expected: EnvironmentProviderResourceState,
) -> DirectLocalProviderStateData:
    parsed = _validate_state_shape(state)
    expected_data = DirectLocalProviderStateData.model_validate(expected.data)
    if parsed != expected_data:
        raise EnvironmentProviderError(
            "Direct Local provider state does not match the resolved specification.",
            code="provider_state_invalid",
            category=EnvironmentProviderErrorCategory.INVALID,
            certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
            recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
            context=EnvironmentProviderErrorContext(
                provider_key=_PROVIDER_KEY,
                state_version=state.state_version,
            ),
        )
    return parsed


def _operation_context(operation: EnvironmentOperationContext) -> EnvironmentProviderErrorContext:
    return EnvironmentProviderErrorContext(
        provider_key=_PROVIDER_KEY,
        action=operation.action,
        operation_id=operation.operation_id,
        resource_correlation=operation.resource_correlation,
    )
