from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from types import TracebackType

from .attachments import EnvironmentRuntimeAttachment
from .errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
)
from .models import (
    EnvironmentLifecycleCapabilities,
    EnvironmentManagementAction,
    EnvironmentOperationContext,
    EnvironmentPauseMode,
    EnvironmentProviderResourceState,
    EnvironmentReconciliationResult,
)


class EnvironmentProviderRuntime:
    """Provider-owned process-local runtime collaborator marker."""


class ManagedEnvironment(ABC):
    """Single-entry process-local scope for one identified provider resource."""

    def __init__(self) -> None:
        self._scope_status = "new"

    async def __aenter__(self) -> ManagedEnvironment:
        if self._scope_status != "new":
            raise EnvironmentProviderError(
                "ManagedEnvironment scopes can be entered exactly once.",
                code="provider_conflict",
                category=EnvironmentProviderErrorCategory.CONFLICT,
                certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                recovery_hint=EnvironmentProviderRecoveryHint.NONE,
                context=EnvironmentProviderErrorContext(provider_key=self.state.provider_key),
            )
        self._scope_status = "entering"
        try:
            await self._enter_scope()
        except BaseException:
            self._scope_status = "closed"
            raise
        self._scope_status = "entered"
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        del exc_type, exc_value, traceback
        if self._scope_status != "entered":
            return None
        self._scope_status = "closing"
        try:
            await self._exit_scope()
        finally:
            self._scope_status = "closed"
        return None

    @property
    @abstractmethod
    def state(self) -> EnvironmentProviderResourceState:
        """Return the latest validated provider state observation."""

    @abstractmethod
    def acquire_attachment(self) -> AbstractAsyncContextManager[EnvironmentRuntimeAttachment]:
        """Acquire one fresh attachment under the provider's concurrency policy."""

    async def _enter_scope(self) -> None:
        """Start process-local clients and attachment admission."""
        return None

    async def _exit_scope(self) -> None:
        """Close process-local clients and attachment admission."""
        return None

    def _require_entered(self) -> None:
        if self._scope_status != "entered":
            raise EnvironmentProviderError(
                "ManagedEnvironment attachment acquisition requires an entered resource scope.",
                code="provider_attachment_conflict",
                category=EnvironmentProviderErrorCategory.CONFLICT,
                certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                context=EnvironmentProviderErrorContext(provider_key=self.state.provider_key),
            )

    @property
    def is_entered(self) -> bool:
        return self._scope_status == "entered"


class EnvironmentManager(ABC):
    """Host-facing lifecycle API for one resolved provider specification."""

    def __init__(self) -> None:
        self._observed_operations: dict[
            str,
            tuple[EnvironmentManagementAction, str, int],
        ] = {}

    @property
    @abstractmethod
    def lifecycle_capabilities(self) -> EnvironmentLifecycleCapabilities: ...

    @abstractmethod
    async def create(self, *, operation: EnvironmentOperationContext) -> ManagedEnvironment: ...

    @abstractmethod
    async def resume(
        self,
        state: EnvironmentProviderResourceState,
        *,
        operation: EnvironmentOperationContext,
    ) -> ManagedEnvironment: ...

    @abstractmethod
    async def pause(
        self,
        environment: ManagedEnvironment,
        *,
        operation: EnvironmentOperationContext,
        mode: EnvironmentPauseMode = EnvironmentPauseMode.FULL,
    ) -> EnvironmentProviderResourceState: ...

    @abstractmethod
    async def destroy(
        self,
        state: EnvironmentProviderResourceState,
        *,
        operation: EnvironmentOperationContext,
    ) -> None: ...

    @abstractmethod
    async def reconcile(
        self,
        operation: EnvironmentOperationContext,
        *,
        last_known_state: EnvironmentProviderResourceState | None,
    ) -> EnvironmentReconciliationResult: ...

    def _require_operation(
        self,
        operation: EnvironmentOperationContext,
        expected: EnvironmentManagementAction,
        *,
        provider_key: str,
    ) -> None:
        if operation.action is not expected:
            raise _operation_error(
                f"Operation action {operation.action.value!r} does not match {expected.value!r}.",
                operation=operation,
                provider_key=provider_key,
                code="provider_spec_invalid",
                category=EnvironmentProviderErrorCategory.INVALID,
                recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
            )

        observed = self._observed_operations.get(operation.operation_id)
        if observed is None:
            self._observed_operations[operation.operation_id] = (
                operation.action,
                operation.resource_correlation,
                operation.attempt,
            )
            return

        action, resource_correlation, latest_attempt = observed
        if action is not operation.action or resource_correlation != operation.resource_correlation:
            raise _operation_error(
                "An operation ID cannot be reused for another action or resource correlation.",
                operation=operation,
                provider_key=provider_key,
                code="provider_conflict",
                category=EnvironmentProviderErrorCategory.CONFLICT,
                recovery_hint=EnvironmentProviderRecoveryHint.NONE,
            )
        if operation.attempt < latest_attempt or operation.attempt > latest_attempt + 1:
            raise _operation_error(
                "Lifecycle operation attempts must be repeated or increase by exactly one.",
                operation=operation,
                provider_key=provider_key,
                code="provider_conflict",
                category=EnvironmentProviderErrorCategory.CONFLICT,
                recovery_hint=EnvironmentProviderRecoveryHint.NONE,
            )
        self._observed_operations[operation.operation_id] = (
            action,
            resource_correlation,
            max(latest_attempt, operation.attempt),
        )

    def _require_reconciliation_operation(
        self,
        operation: EnvironmentOperationContext,
        *,
        provider_key: str,
    ) -> None:
        observed = self._observed_operations.get(operation.operation_id)
        if observed is None:
            return
        action, resource_correlation, latest_attempt = observed
        if (
            action is not operation.action
            or resource_correlation != operation.resource_correlation
            or operation.attempt > latest_attempt
        ):
            raise _operation_error(
                "Reconciliation must inspect an observed operation with its original identity.",
                operation=operation,
                provider_key=provider_key,
                code="provider_conflict",
                category=EnvironmentProviderErrorCategory.CONFLICT,
                recovery_hint=EnvironmentProviderRecoveryHint.NONE,
            )


def _operation_error(
    description: str,
    *,
    operation: EnvironmentOperationContext,
    provider_key: str,
    code: str,
    category: EnvironmentProviderErrorCategory,
    recovery_hint: EnvironmentProviderRecoveryHint,
) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        description,
        code=code,
        category=category,
        certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
        recovery_hint=recovery_hint,
        context=EnvironmentProviderErrorContext(
            provider_key=provider_key,
            action=operation.action,
            operation_id=operation.operation_id,
            resource_correlation=operation.resource_correlation,
        ),
    )
