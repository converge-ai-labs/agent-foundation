"""A Host-facing Environment provider plugin backed by Direct Local attachments."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from a13n_environment_provider import (
    DirectLocalEnvironmentAttachment,
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
    EnvironmentAttachmentConcurrency,
    EnvironmentLifecycleCapabilities,
    EnvironmentManagementAction,
    EnvironmentOperationContext,
    EnvironmentPauseMode,
    EnvironmentProvider,
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderFactory,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
    EnvironmentProviderResourceState,
    EnvironmentProviderRuntime,
    EnvironmentReconciliationPhase,
    EnvironmentReconciliationResult,
    EnvironmentResource,
    EnvironmentResourceAllocation,
    EnvironmentRuntimeAttachment,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

PROVIDER_KEY = "example.workspace"
_CAPABILITIES = EnvironmentLifecycleCapabilities(
    pause_modes=frozenset(),
    resource_allocation=EnvironmentResourceAllocation.SINGLE_FROM_SPEC,
    attachment_concurrency=EnvironmentAttachmentConcurrency.SHARED,
)


class WorkspaceEnvironmentConfiguration(BaseModel):
    """Credential-free schema version 1 configuration for the example provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    root: Path
    environment_id: str = Field(min_length=1, max_length=128)
    read_only: bool = True

    @field_validator("root")
    @classmethod
    def _absolute_root(cls, value: Path) -> Path:
        expanded = value.expanduser()
        if "\x00" in str(expanded):
            raise ValueError("root must not contain NUL")
        if not expanded.is_absolute():
            raise ValueError("root must be absolute")
        return expanded


class WorkspaceEnvironmentStateData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment_id: str = Field(min_length=1, max_length=128)
    configuration_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class WorkspaceEnvironmentRuntime(EnvironmentProviderRuntime):
    """This deterministic provider needs no credential or SDK collaborator."""


class WorkspaceEnvironmentProviderFactory(EnvironmentProviderFactory):
    """Construct an inert Provider for the selected exact provider specification."""

    @classmethod
    def provider_key(cls) -> str:
        return PROVIDER_KEY

    @classmethod
    def supported_schema_versions(cls) -> frozenset[str]:
        return frozenset({"1"})

    @classmethod
    def configuration_model(cls, schema_version: str) -> type[BaseModel]:
        if schema_version != "1":
            raise _error(
                "Unsupported example.workspace schema version.",
                code="provider_schema_unsupported",
                category=EnvironmentProviderErrorCategory.UNSUPPORTED,
            )
        return WorkspaceEnvironmentConfiguration

    def lifecycle_capabilities(
        self,
        configuration: BaseModel,
    ) -> EnvironmentLifecycleCapabilities:
        if not isinstance(configuration, WorkspaceEnvironmentConfiguration):
            raise _error("Invalid example.workspace configuration.", code="provider_spec_invalid")
        return _CAPABILITIES

    def create_provider(
        self,
        configuration: BaseModel,
        *,
        runtime: EnvironmentProviderRuntime,
    ) -> EnvironmentProvider:
        if not isinstance(configuration, WorkspaceEnvironmentConfiguration):
            raise _error("Invalid example.workspace configuration.", code="provider_spec_invalid")
        if not isinstance(runtime, WorkspaceEnvironmentRuntime):
            raise _error("Invalid example.workspace runtime.", code="provider_runtime_invalid")
        return WorkspaceEnvironmentProvider(configuration)


class WorkspaceEnvironmentProvider(EnvironmentProvider):
    """Manage one logical shared workspace without owning its directory lifecycle."""

    def __init__(self, configuration: WorkspaceEnvironmentConfiguration) -> None:
        super().__init__()
        self._configuration = configuration.model_copy(deep=True)

    @property
    def lifecycle_capabilities(self) -> EnvironmentLifecycleCapabilities:
        return _CAPABILITIES

    async def create(self, *, operation: EnvironmentOperationContext) -> EnvironmentResource:
        self._require_operation(operation, EnvironmentManagementAction.CREATE, provider_key=PROVIDER_KEY)
        configuration = await _validated_attachment_configuration(self._configuration)
        state = _state_for(self._canonical_configuration(configuration))
        return WorkspaceEnvironmentResource(configuration, state)

    async def resume(
        self,
        state: EnvironmentProviderResourceState,
        *,
        operation: EnvironmentOperationContext,
    ) -> EnvironmentResource:
        self._require_operation(operation, EnvironmentManagementAction.RESUME, provider_key=PROVIDER_KEY)
        configuration = await _validated_attachment_configuration(self._configuration)
        expected = _state_for(self._canonical_configuration(configuration))
        _validate_state(state, expected)
        return WorkspaceEnvironmentResource(configuration, expected)

    async def pause(
        self,
        environment: EnvironmentResource,
        *,
        operation: EnvironmentOperationContext,
        mode: EnvironmentPauseMode = EnvironmentPauseMode.FULL,
    ) -> EnvironmentProviderResourceState:
        del environment, mode
        self._require_operation(operation, EnvironmentManagementAction.PAUSE, provider_key=PROVIDER_KEY)
        raise _error(
            "example.workspace does not support pause.",
            code="provider_action_unsupported",
            category=EnvironmentProviderErrorCategory.UNSUPPORTED,
            operation=operation,
        )

    async def destroy(
        self,
        state: EnvironmentProviderResourceState,
        *,
        operation: EnvironmentOperationContext,
    ) -> None:
        self._require_operation(operation, EnvironmentManagementAction.DESTROY, provider_key=PROVIDER_KEY)
        phase = await asyncio.to_thread(_inspect_root, self._configuration.root)
        if phase is EnvironmentReconciliationPhase.RUNNING:
            configuration = await _validated_attachment_configuration(self._configuration)
            _validate_state(state, _state_for(self._canonical_configuration(configuration)))
        else:
            _validate_state_shape(state, environment_id=self._configuration.environment_id)

    async def reconcile(
        self,
        operation: EnvironmentOperationContext,
        *,
        last_known_state: EnvironmentProviderResourceState | None,
    ) -> EnvironmentReconciliationResult:
        self._require_reconciliation_operation(operation, provider_key=PROVIDER_KEY)
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
        if operation.action is EnvironmentManagementAction.PAUSE:
            raise _error(
                "example.workspace does not support pause.",
                code="provider_action_unsupported",
                category=EnvironmentProviderErrorCategory.UNSUPPORTED,
                operation=operation,
            )
        phase = await asyncio.to_thread(_inspect_root, self._configuration.root)
        state = None
        if phase is EnvironmentReconciliationPhase.RUNNING:
            configuration = await _validated_attachment_configuration(self._configuration)
            state = _state_for(self._canonical_configuration(configuration))
            if last_known_state is not None:
                _validate_state(last_known_state, state)
        elif last_known_state is not None:
            _validate_state_shape(
                last_known_state,
                environment_id=self._configuration.environment_id,
            )
        evidence = {
            EnvironmentReconciliationPhase.RUNNING: "accessible_directory",
            EnvironmentReconciliationPhase.ABSENT: "absent",
            EnvironmentReconciliationPhase.UNKNOWN: "unavailable",
        }[phase]
        return EnvironmentReconciliationResult(
            operation_id=operation.operation_id,
            phase=phase,
            state=state,
            evidence={"root": evidence},
        )

    def _canonical_configuration(
        self,
        attachment_configuration: DirectLocalProviderConfiguration,
    ) -> WorkspaceEnvironmentConfiguration:
        return self._configuration.model_copy(
            update={"root": attachment_configuration.root.path},
            deep=True,
        )


class WorkspaceEnvironmentResource(EnvironmentResource):
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

    @property
    def state(self) -> EnvironmentProviderResourceState:
        return self._state

    @asynccontextmanager
    async def acquire_attachment(self) -> AsyncGenerator[EnvironmentRuntimeAttachment]:
        self._require_entered()
        self._attachment_sequence += 1
        self._active_attachments += 1
        attachment = DirectLocalEnvironmentAttachment(
            attachment_id=f"attachment-{self._attachment_sequence}",
            environment_id=self._configuration.environment_id,
            configuration=self._configuration,
        )
        try:
            yield attachment
        finally:
            self._active_attachments -= 1

    async def _exit_scope(self) -> None:
        if self._active_attachments:
            raise _error(
                "example.workspace closed with active attachments.",
                code="provider_cleanup_failed",
                category=EnvironmentProviderErrorCategory.CLEANUP,
            )


def _state_for(configuration: WorkspaceEnvironmentConfiguration) -> EnvironmentProviderResourceState:
    encoded = json.dumps(
        configuration.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return EnvironmentProviderResourceState(
        provider_key=PROVIDER_KEY,
        state_version="1",
        data={
            "environment_id": configuration.environment_id,
            "configuration_fingerprint": f"sha256:{hashlib.sha256(encoded).hexdigest()}",
        },
    )


async def _validated_attachment_configuration(
    configuration: WorkspaceEnvironmentConfiguration,
) -> DirectLocalProviderConfiguration:
    root = await asyncio.to_thread(_resolve_root, configuration.root)
    return DirectLocalProviderConfiguration(
        environment_id=configuration.environment_id,
        root=DirectLocalRootConfiguration(path=root, read_only=configuration.read_only),
    )


def _resolve_root(path: Path) -> Path:
    try:
        root = path.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        raise _error("The selected workspace does not exist.", code="provider_resource_missing") from exc
    if not root.is_dir():
        raise _error("The selected workspace is not a directory.", code="provider_spec_invalid")
    return root


def _inspect_root(path: Path) -> EnvironmentReconciliationPhase:
    try:
        return (
            EnvironmentReconciliationPhase.RUNNING
            if path.resolve(strict=True).is_dir()
            else EnvironmentReconciliationPhase.ABSENT
        )
    except (FileNotFoundError, NotADirectoryError):
        return EnvironmentReconciliationPhase.ABSENT
    except (OSError, ValueError):
        return EnvironmentReconciliationPhase.UNKNOWN


def _validate_state_shape(
    state: EnvironmentProviderResourceState,
    *,
    environment_id: str | None = None,
) -> None:
    if state.provider_key != PROVIDER_KEY or state.state_version != "1":
        raise _error("Incompatible example.workspace state.", code="provider_state_invalid")
    try:
        parsed = WorkspaceEnvironmentStateData.model_validate(state.data)
    except ValidationError as exc:
        raise _error("Incompatible example.workspace state.", code="provider_state_invalid") from exc
    if environment_id is not None and parsed.environment_id != environment_id:
        raise _error("Incompatible example.workspace state.", code="provider_state_invalid")


def _validate_state(
    state: EnvironmentProviderResourceState,
    expected: EnvironmentProviderResourceState,
) -> None:
    _validate_state_shape(state)
    if state != expected:
        raise _error("Incompatible example.workspace state.", code="provider_state_invalid")


def _error(
    description: str,
    *,
    code: str,
    category: EnvironmentProviderErrorCategory = EnvironmentProviderErrorCategory.INVALID,
    operation: EnvironmentOperationContext | None = None,
) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        description,
        code=code,
        category=category,
        certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
        recovery_hint=(
            EnvironmentProviderRecoveryHint.NONE
            if category is EnvironmentProviderErrorCategory.UNSUPPORTED
            else EnvironmentProviderRecoveryHint.FIX_INPUT
        ),
        context=EnvironmentProviderErrorContext(
            provider_key=PROVIDER_KEY,
            action=operation.action if operation is not None else None,
            operation_id=operation.operation_id if operation is not None else None,
            resource_correlation=operation.resource_correlation if operation is not None else None,
        ),
    )
