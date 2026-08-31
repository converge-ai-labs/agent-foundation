from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import AsyncGenerator, Coroutine
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from types import TracebackType
from typing import Any
from uuid import uuid4

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
    EnvironmentReconciliationPhase,
    EnvironmentReconciliationResult,
)


class EnvironmentProviderRuntime:
    """Provider-owned process-local runtime collaborator marker."""


class EnvironmentResource(ABC):
    """Single-entry process-local scope for one identified provider resource."""

    def __init__(self) -> None:
        self._scope_status = "new"

    async def __aenter__(self) -> EnvironmentResource:
        if self._scope_status != "new":
            raise EnvironmentProviderError(
                "EnvironmentResource scopes can be entered exactly once.",
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
        del exc_type, traceback
        if self._scope_status != "entered":
            return None
        cleanup_error: BaseException | None = None
        try:
            await self.close()
        except BaseException as error:
            cleanup_error = error

        if cleanup_error is None:
            return None
        if isinstance(exc_value, asyncio.CancelledError):
            exc_value.add_note(f"Environment resource exit also failed: {cleanup_error!r}")
            return None
        if isinstance(cleanup_error, asyncio.CancelledError):
            if exc_value is not None:
                cleanup_error.add_note(f"Environment resource use also failed: {exc_value!r}")
            raise cleanup_error from None
        if exc_value is not None:
            raise BaseExceptionGroup(
                "Environment resource use and exit failed",
                [exc_value, cleanup_error],
            ) from None
        raise cleanup_error

    async def close(self) -> None:
        """Close only this process-local resource scope."""
        if self._scope_status != "entered":
            return
        self._scope_status = "closing"
        try:
            await self._exit_scope()
        finally:
            self._scope_status = "closed"

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
                "EnvironmentResource attachment acquisition requires an entered resource scope.",
                code="provider_attachment_conflict",
                category=EnvironmentProviderErrorCategory.CONFLICT,
                certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
                context=EnvironmentProviderErrorContext(provider_key=self.state.provider_key),
            )

    @property
    def is_entered(self) -> bool:
        return self._scope_status == "entered"


class EnvironmentProvider(ABC):
    """Host-facing lifecycle API for one resolved provider specification."""

    _MAX_OBSERVED_OPERATIONS = 1024

    def __init__(self) -> None:
        self._observed_operations: OrderedDict[
            str,
            tuple[EnvironmentManagementAction, str, int],
        ] = OrderedDict()

    @property
    @abstractmethod
    def lifecycle_capabilities(self) -> EnvironmentLifecycleCapabilities: ...

    @abstractmethod
    async def create(self, *, operation: EnvironmentOperationContext) -> EnvironmentResource: ...

    @abstractmethod
    async def resume(
        self,
        state: EnvironmentProviderResourceState,
        *,
        operation: EnvironmentOperationContext,
    ) -> EnvironmentResource: ...

    @abstractmethod
    async def pause(
        self,
        environment: EnvironmentResource,
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

    @asynccontextmanager
    async def resource_scope(
        self,
        resource: EnvironmentResource,
        *,
        destroy_on_exit: bool = False,
        destroy_operation: EnvironmentOperationContext | None = None,
    ) -> AsyncGenerator[EnvironmentResource]:
        """Enter one Resource and optionally destroy it after local scope exit."""
        if destroy_on_exit != (destroy_operation is not None):
            raise ValueError("destroy_on_exit and destroy_operation must be selected together")

        state = resource.state
        primary_error: BaseException | None = None
        try:
            try:
                async with resource:
                    yield resource
            finally:
                state = resource.state
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            if destroy_on_exit:
                assert destroy_operation is not None
                try:
                    await self._destroy_resource(state, destroy_operation)
                except BaseException as cleanup_error:
                    if isinstance(primary_error, asyncio.CancelledError):
                        primary_error.add_note(f"Environment resource destruction also failed: {cleanup_error!r}")
                    elif isinstance(cleanup_error, asyncio.CancelledError):
                        if primary_error is not None:
                            cleanup_error.add_note(f"Environment resource use also failed: {primary_error!r}")
                        raise
                    elif primary_error is not None:
                        raise BaseExceptionGroup(
                            "Environment resource use and destruction failed",
                            [primary_error, cleanup_error],
                        ) from None
                    else:
                        raise

    @asynccontextmanager
    async def ephemeral(
        self,
        *,
        resource_correlation: str | None = None,
    ) -> AsyncGenerator[EnvironmentResource]:
        """Create, enter, and destroy one temporary provider resource."""
        correlation = resource_correlation or f"resource-{uuid4().hex}"
        create_operation = _new_operation(EnvironmentManagementAction.CREATE, correlation)
        try:
            resource = await self._create_ephemeral_resource(create_operation)
        except asyncio.CancelledError as cancellation:
            try:
                await _await_cleanup(
                    self._cleanup_cancelled_ephemeral_create(create_operation),
                    name="environment-provider-cancelled-create-cleanup",
                )
            except BaseException as cleanup_error:
                cancellation.add_note(f"Cancelled environment create reconciliation also failed: {cleanup_error!r}")
            raise cancellation from None
        async with self.resource_scope(
            resource,
            destroy_on_exit=True,
            destroy_operation=_new_operation(EnvironmentManagementAction.DESTROY, correlation),
        ) as entered:
            yield entered

    async def _create_ephemeral_resource(
        self,
        operation: EnvironmentOperationContext,
    ) -> EnvironmentResource:
        current = operation
        for dispatch_attempt in range(2):
            try:
                return await self.create(operation=current)
            except EnvironmentProviderError as error:
                if error.certainty is not EnvironmentProviderOutcomeCertainty.UNKNOWN:
                    raise
                reconciled = await self.reconcile(current, last_known_state=None)
                _require_reconciliation_identity(current, reconciled)
                if reconciled.phase in {
                    EnvironmentReconciliationPhase.RUNNING,
                    EnvironmentReconciliationPhase.PAUSED,
                }:
                    assert reconciled.state is not None
                    return await self._resume_ephemeral_resource(
                        reconciled.state,
                        resource_correlation=current.resource_correlation,
                    )
                if reconciled.phase is EnvironmentReconciliationPhase.ABSENT and dispatch_attempt == 0:
                    current = current.model_copy(update={"attempt": current.attempt + 1})
                    continue
                raise
        raise AssertionError("ephemeral create retry loop exhausted")

    async def _cleanup_cancelled_ephemeral_create(
        self,
        operation: EnvironmentOperationContext,
    ) -> None:
        reconciled = await self.reconcile(operation, last_known_state=None)
        _require_reconciliation_identity(operation, reconciled)
        if reconciled.phase is EnvironmentReconciliationPhase.ABSENT:
            return
        if reconciled.phase in {
            EnvironmentReconciliationPhase.RUNNING,
            EnvironmentReconciliationPhase.PAUSED,
        }:
            assert reconciled.state is not None
            await self._destroy_resource(
                reconciled.state,
                _new_operation(
                    EnvironmentManagementAction.DESTROY,
                    operation.resource_correlation,
                ),
            )
            return
        raise EnvironmentProviderError(
            "Cancelled environment create reconciliation did not reach a terminal phase.",
            code="provider_unknown_outcome",
            category=EnvironmentProviderErrorCategory.UNKNOWN_OUTCOME,
            certainty=EnvironmentProviderOutcomeCertainty.UNKNOWN,
            recovery_hint=EnvironmentProviderRecoveryHint.RECONCILE,
            context=EnvironmentProviderErrorContext(
                action=operation.action,
                operation_id=operation.operation_id,
                resource_correlation=operation.resource_correlation,
            ),
        )

    async def _resume_ephemeral_resource(
        self,
        state: EnvironmentProviderResourceState,
        *,
        resource_correlation: str,
    ) -> EnvironmentResource:
        try:
            return await self.resume(
                state,
                operation=_new_operation(EnvironmentManagementAction.RESUME, resource_correlation),
            )
        except BaseException as primary_error:
            try:
                await self._destroy_resource(
                    state,
                    _new_operation(EnvironmentManagementAction.DESTROY, resource_correlation),
                )
            except BaseException as cleanup_error:
                if isinstance(primary_error, asyncio.CancelledError):
                    primary_error.add_note(f"Environment resource destruction also failed: {cleanup_error!r}")
                    raise primary_error from None
                if isinstance(cleanup_error, asyncio.CancelledError):
                    cleanup_error.add_note(f"Environment resource resume also failed: {primary_error!r}")
                    raise cleanup_error from None
                raise BaseExceptionGroup(
                    "Environment resource resume and destruction failed",
                    [primary_error, cleanup_error],
                ) from None
            raise

    async def _destroy_resource(
        self,
        state: EnvironmentProviderResourceState,
        operation: EnvironmentOperationContext,
    ) -> None:
        current = operation
        for dispatch_attempt in range(2):
            try:
                await self.destroy(state, operation=current)
                return
            except EnvironmentProviderError as error:
                if error.certainty is not EnvironmentProviderOutcomeCertainty.UNKNOWN:
                    raise
                reconciled = await self.reconcile(current, last_known_state=state)
                _require_reconciliation_identity(current, reconciled)
                if reconciled.phase is EnvironmentReconciliationPhase.ABSENT:
                    return
                if (
                    reconciled.phase in {EnvironmentReconciliationPhase.RUNNING, EnvironmentReconciliationPhase.PAUSED}
                    and dispatch_attempt == 0
                ):
                    assert reconciled.state is not None
                    state = reconciled.state
                    current = current.model_copy(update={"attempt": current.attempt + 1})
                    continue
                raise
        raise AssertionError("ephemeral destroy retry loop exhausted")

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
            if len(self._observed_operations) > self._MAX_OBSERVED_OPERATIONS:
                self._observed_operations.popitem(last=False)
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
        self._observed_operations.move_to_end(operation.operation_id)

    def _require_reconciliation_operation(
        self,
        operation: EnvironmentOperationContext,
        *,
        provider_key: str,
    ) -> None:
        observed = self._observed_operations.get(operation.operation_id)
        if observed is None:
            return
        self._observed_operations.move_to_end(operation.operation_id)
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


def _require_reconciliation_identity(
    operation: EnvironmentOperationContext,
    result: EnvironmentReconciliationResult,
) -> None:
    if result.operation_id != operation.operation_id:
        raise EnvironmentProviderError(
            "Provider reconciliation returned another operation identity.",
            code="provider_failure",
            category=EnvironmentProviderErrorCategory.PROVIDER_FAILURE,
            certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
            context=EnvironmentProviderErrorContext(
                action=operation.action,
                operation_id=operation.operation_id,
                resource_correlation=operation.resource_correlation,
            ),
        )


async def _await_cleanup[ResultT](
    coroutine: Coroutine[Any, Any, ResultT],
    *,
    name: str,
) -> ResultT:
    task = asyncio.create_task(coroutine, name=name)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


def _new_operation(
    action: EnvironmentManagementAction,
    resource_correlation: str,
) -> EnvironmentOperationContext:
    return EnvironmentOperationContext(
        operation_id=f"operation-{action.value}-{uuid4().hex}",
        action=action,
        resource_correlation=resource_correlation,
        attempt=1,
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
