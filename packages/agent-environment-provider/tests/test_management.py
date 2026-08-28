from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import cast

import pytest
from a13n_environment_provider import (
    EnvironmentAttachmentConcurrency,
    EnvironmentLifecycleCapabilities,
    EnvironmentManagementAction,
    EnvironmentOperationContext,
    EnvironmentPauseMode,
    EnvironmentProvider,
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
    EnvironmentProviderResourceState,
    EnvironmentReconciliationPhase,
    EnvironmentReconciliationResult,
    EnvironmentResource,
    EnvironmentResourceAllocation,
    EnvironmentRuntimeAttachment,
)

pytestmark = pytest.mark.anyio

_STATE = EnvironmentProviderResourceState(
    provider_key="test.provider",
    state_version="1",
    data={"resource": "one"},
)
_UPDATED_STATE = EnvironmentProviderResourceState(
    provider_key="test.provider",
    state_version="1",
    data={"resource": "one", "revision": 2},
)
_CAPABILITIES = EnvironmentLifecycleCapabilities(
    pause_modes=frozenset({EnvironmentPauseMode.FULL}),
    resource_allocation=EnvironmentResourceAllocation.MULTIPLE_FROM_SPEC,
    attachment_concurrency=EnvironmentAttachmentConcurrency.SHARED,
)


class _Resource(EnvironmentResource):
    def __init__(
        self,
        events: list[str],
        *,
        enter_error: BaseException | None = None,
        exit_error: BaseException | None = None,
        exit_state: EnvironmentProviderResourceState | None = None,
    ) -> None:
        super().__init__()
        self._events = events
        self._enter_error = enter_error
        self._exit_error = exit_error
        self._exit_state = exit_state
        self._state = _STATE

    @property
    def state(self) -> EnvironmentProviderResourceState:
        return self._state

    def acquire_attachment(self) -> AbstractAsyncContextManager[EnvironmentRuntimeAttachment]:
        @asynccontextmanager
        async def unavailable() -> AsyncGenerator[EnvironmentRuntimeAttachment]:
            raise AssertionError("attachment acquisition is not used by these tests")
            yield cast(EnvironmentRuntimeAttachment, object())

        return unavailable()

    async def _enter_scope(self) -> None:
        self._events.append("enter")
        if self._enter_error is not None:
            raise self._enter_error

    async def _exit_scope(self) -> None:
        self._events.append("exit")
        if self._exit_state is not None:
            self._state = self._exit_state
        if self._exit_error is not None:
            raise self._exit_error


class _Provider(EnvironmentProvider):
    def __init__(
        self,
        *,
        create_outcomes: list[str] | None = None,
        destroy_outcomes: list[str] | None = None,
        reconciliation: list[EnvironmentReconciliationResult] | None = None,
        enter_error: BaseException | None = None,
        exit_error: BaseException | None = None,
        exit_state: EnvironmentProviderResourceState | None = None,
    ) -> None:
        super().__init__()
        self.events: list[str] = []
        self.create_outcomes = create_outcomes or ["success"]
        self.destroy_outcomes = destroy_outcomes or ["success"]
        self.reconciliation = reconciliation or []
        self.enter_error = enter_error
        self.exit_error = exit_error
        self.exit_state = exit_state
        self.create_operations: list[EnvironmentOperationContext] = []
        self.resume_operations: list[EnvironmentOperationContext] = []
        self.destroy_operations: list[EnvironmentOperationContext] = []
        self.destroy_states: list[EnvironmentProviderResourceState] = []
        self.reconcile_operations: list[EnvironmentOperationContext] = []

    @property
    def lifecycle_capabilities(self) -> EnvironmentLifecycleCapabilities:
        return _CAPABILITIES

    async def create(self, *, operation: EnvironmentOperationContext) -> EnvironmentResource:
        self._require_operation(operation, EnvironmentManagementAction.CREATE, provider_key="test.provider")
        self.create_operations.append(operation)
        self.events.append(f"create:{operation.attempt}")
        outcome = self.create_outcomes.pop(0)
        if outcome == "unknown":
            raise _unknown_error(operation)
        if outcome == "failure":
            raise RuntimeError("create failed")
        return _Resource(
            self.events,
            enter_error=self.enter_error,
            exit_error=self.exit_error,
            exit_state=self.exit_state,
        )

    async def resume(
        self,
        state: EnvironmentProviderResourceState,
        *,
        operation: EnvironmentOperationContext,
    ) -> EnvironmentResource:
        assert state == _STATE
        self._require_operation(operation, EnvironmentManagementAction.RESUME, provider_key="test.provider")
        self.resume_operations.append(operation)
        self.events.append("resume")
        return _Resource(
            self.events,
            enter_error=self.enter_error,
            exit_error=self.exit_error,
            exit_state=self.exit_state,
        )

    async def pause(
        self,
        environment: EnvironmentResource,
        *,
        operation: EnvironmentOperationContext,
        mode: EnvironmentPauseMode = EnvironmentPauseMode.FULL,
    ) -> EnvironmentProviderResourceState:
        del environment, mode
        self._require_operation(operation, EnvironmentManagementAction.PAUSE, provider_key="test.provider")
        return _STATE

    async def destroy(
        self,
        state: EnvironmentProviderResourceState,
        *,
        operation: EnvironmentOperationContext,
    ) -> None:
        self._require_operation(operation, EnvironmentManagementAction.DESTROY, provider_key="test.provider")
        self.destroy_operations.append(operation)
        self.destroy_states.append(state)
        self.events.append(f"destroy:{operation.attempt}")
        outcome = self.destroy_outcomes.pop(0)
        if outcome == "unknown":
            raise _unknown_error(operation)
        if outcome == "failure":
            raise RuntimeError("destroy failed")

    async def reconcile(
        self,
        operation: EnvironmentOperationContext,
        *,
        last_known_state: EnvironmentProviderResourceState | None,
    ) -> EnvironmentReconciliationResult:
        del last_known_state
        self._require_reconciliation_operation(operation, provider_key="test.provider")
        self.reconcile_operations.append(operation)
        self.events.append(f"reconcile:{operation.action.value}:{operation.attempt}")
        return self.reconciliation.pop(0)


def _unknown_error(operation: EnvironmentOperationContext) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        "outcome is unknown",
        code="provider_unknown_outcome",
        category=EnvironmentProviderErrorCategory.UNKNOWN_OUTCOME,
        certainty=EnvironmentProviderOutcomeCertainty.UNKNOWN,
        recovery_hint=EnvironmentProviderRecoveryHint.RECONCILE,
        context=EnvironmentProviderErrorContext(
            provider_key="test.provider",
            action=operation.action,
            operation_id=operation.operation_id,
            resource_correlation=operation.resource_correlation,
        ),
    )


def _result(
    operation_id: str,
    phase: EnvironmentReconciliationPhase,
) -> EnvironmentReconciliationResult:
    return EnvironmentReconciliationResult(
        operation_id=operation_id,
        phase=phase,
        state=_STATE
        if phase in {EnvironmentReconciliationPhase.RUNNING, EnvironmentReconciliationPhase.PAUSED}
        else None,
    )


async def test_ephemeral_owns_create_entry_exit_and_destroy_in_order() -> None:
    provider = _Provider()

    async with provider.ephemeral(resource_correlation="resource-test") as resource:
        assert resource.is_entered
        provider.events.append("body")

    assert provider.events == ["create:1", "enter", "body", "exit", "destroy:1"]
    create = provider.create_operations[0]
    destroy = provider.destroy_operations[0]
    assert create.resource_correlation == destroy.resource_correlation == "resource-test"
    assert create.operation_id != destroy.operation_id
    assert create.action is EnvironmentManagementAction.CREATE
    assert destroy.action is EnvironmentManagementAction.DESTROY


async def test_ephemeral_destroys_latest_state_observed_during_resource_exit() -> None:
    provider = _Provider(exit_state=_UPDATED_STATE)

    async with provider.ephemeral():
        pass

    assert provider.destroy_states == [_UPDATED_STATE]


async def test_ephemeral_retries_absent_uncertain_create_with_same_operation_identity() -> None:
    provider = _Provider(create_outcomes=["unknown", "success"])

    original_create = provider.create

    async def create(*, operation: EnvironmentOperationContext) -> EnvironmentResource:
        if not provider.reconciliation:
            provider.reconciliation.append(_result(operation.operation_id, EnvironmentReconciliationPhase.ABSENT))
        return await original_create(operation=operation)

    provider.create = create  # type: ignore[method-assign]
    async with provider.ephemeral():
        pass

    first, second = provider.create_operations
    assert first.operation_id == second.operation_id
    assert [first.attempt, second.attempt] == [1, 2]
    assert provider.reconcile_operations == [first]


async def test_ephemeral_resumes_resource_found_by_create_reconciliation() -> None:
    provider = _Provider(create_outcomes=["unknown"])

    original_create = provider.create

    async def create(*, operation: EnvironmentOperationContext) -> EnvironmentResource:
        provider.reconciliation.append(_result(operation.operation_id, EnvironmentReconciliationPhase.RUNNING))
        return await original_create(operation=operation)

    provider.create = create  # type: ignore[method-assign]
    async with provider.ephemeral():
        pass

    assert len(provider.create_operations) == 1
    assert len(provider.resume_operations) == 1
    assert provider.events == ["create:1", "reconcile:create:1", "resume", "enter", "exit", "destroy:1"]


async def test_ephemeral_retries_uncertain_destroy_after_running_reconciliation() -> None:
    provider = _Provider(destroy_outcomes=["unknown", "success"])

    original_destroy = provider.destroy

    async def destroy(
        state: EnvironmentProviderResourceState,
        *,
        operation: EnvironmentOperationContext,
    ) -> None:
        if not provider.reconciliation:
            provider.reconciliation.append(_result(operation.operation_id, EnvironmentReconciliationPhase.RUNNING))
        await original_destroy(state, operation=operation)

    provider.destroy = destroy  # type: ignore[method-assign]
    async with provider.ephemeral():
        pass

    first, second = provider.destroy_operations
    assert first.operation_id == second.operation_id
    assert [first.attempt, second.attempt] == [1, 2]
    assert provider.reconcile_operations == [first]


async def test_ephemeral_destroys_created_state_when_resource_entry_fails() -> None:
    provider = _Provider(enter_error=RuntimeError("enter failed"))

    with pytest.raises(RuntimeError, match="enter failed"):
        async with provider.ephemeral():
            raise AssertionError("body must not start")

    assert provider.events == ["create:1", "enter", "destroy:1"]


async def test_ephemeral_reconciles_and_destroys_create_cancelled_after_dispatch() -> None:
    provider = _Provider()
    dispatched = asyncio.Event()

    async def create(*, operation: EnvironmentOperationContext) -> EnvironmentResource:
        provider._require_operation(operation, EnvironmentManagementAction.CREATE, provider_key="test.provider")
        provider.create_operations.append(operation)
        provider.events.append(f"create:{operation.attempt}")
        provider.reconciliation.append(_result(operation.operation_id, EnvironmentReconciliationPhase.RUNNING))
        dispatched.set()
        await asyncio.Future()
        raise AssertionError("cancelled create must not return")

    async def use_provider() -> None:
        async with provider.ephemeral(resource_correlation="resource-cancelled"):
            raise AssertionError("body must not start")

    provider.create = create  # type: ignore[method-assign]
    task = asyncio.create_task(use_provider())
    await dispatched.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert provider.reconcile_operations == provider.create_operations
    assert provider.destroy_states == [_STATE]
    assert provider.destroy_operations[0].resource_correlation == "resource-cancelled"
    assert provider.events == ["create:1", "reconcile:create:1", "destroy:1"]


async def test_ephemeral_accepts_absent_reconciliation_for_cancelled_create() -> None:
    provider = _Provider()
    cancellation = asyncio.CancelledError("stop")

    async def create(*, operation: EnvironmentOperationContext) -> EnvironmentResource:
        provider._require_operation(operation, EnvironmentManagementAction.CREATE, provider_key="test.provider")
        provider.create_operations.append(operation)
        provider.events.append(f"create:{operation.attempt}")
        provider.reconciliation.append(_result(operation.operation_id, EnvironmentReconciliationPhase.ABSENT))
        raise cancellation

    provider.create = create  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError) as exc_info:
        async with provider.ephemeral():
            raise AssertionError("body must not start")

    assert exc_info.value is cancellation
    assert provider.reconcile_operations == provider.create_operations
    assert provider.destroy_operations == []
    assert provider.events == ["create:1", "reconcile:create:1"]


async def test_ephemeral_notes_unresolved_cancelled_create_reconciliation() -> None:
    provider = _Provider()
    cancellation = asyncio.CancelledError("stop")

    async def create(*, operation: EnvironmentOperationContext) -> EnvironmentResource:
        provider._require_operation(operation, EnvironmentManagementAction.CREATE, provider_key="test.provider")
        provider.create_operations.append(operation)
        provider.reconciliation.append(_result(operation.operation_id, EnvironmentReconciliationPhase.UNKNOWN))
        raise cancellation

    provider.create = create  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError) as exc_info:
        async with provider.ephemeral():
            raise AssertionError("body must not start")

    assert exc_info.value is cancellation
    assert provider.destroy_operations == []
    assert any("reconciliation also failed" in note for note in cancellation.__notes__)


async def test_ephemeral_preserves_cancellation_when_resource_exit_fails() -> None:
    provider = _Provider(exit_error=RuntimeError("exit failed"))
    cancellation = asyncio.CancelledError("stop")

    with pytest.raises(asyncio.CancelledError) as exc_info:
        async with provider.ephemeral():
            raise cancellation

    assert exc_info.value is cancellation
    assert any("exit also failed" in note for note in cancellation.__notes__)
    assert provider.events == ["create:1", "enter", "exit", "destroy:1"]


async def test_ephemeral_preserves_use_and_cleanup_failures() -> None:
    provider = _Provider(destroy_outcomes=["failure"])
    body_error = ValueError("body failed")

    with pytest.raises(BaseExceptionGroup) as exc_info:
        async with provider.ephemeral():
            raise body_error

    assert exc_info.value.exceptions[0] is body_error
    assert isinstance(exc_info.value.exceptions[1], RuntimeError)


async def test_ephemeral_preserves_cancellation_and_notes_cleanup_failure() -> None:
    provider = _Provider(destroy_outcomes=["failure"])
    cancellation = asyncio.CancelledError("stop")

    with pytest.raises(asyncio.CancelledError) as exc_info:
        async with provider.ephemeral():
            raise cancellation

    assert exc_info.value is cancellation
    assert any("destruction also failed" in note for note in cancellation.__notes__)


async def test_ephemeral_preserves_cancelled_resume_when_destruction_fails() -> None:
    provider = _Provider(create_outcomes=["unknown"], destroy_outcomes=["failure"])
    cancellation = asyncio.CancelledError("stop")

    original_create = provider.create

    async def create(*, operation: EnvironmentOperationContext) -> EnvironmentResource:
        provider.reconciliation.append(_result(operation.operation_id, EnvironmentReconciliationPhase.RUNNING))
        return await original_create(operation=operation)

    async def resume(
        state: EnvironmentProviderResourceState,
        *,
        operation: EnvironmentOperationContext,
    ) -> EnvironmentResource:
        del state, operation
        raise cancellation

    provider.create = create  # type: ignore[method-assign]
    provider.resume = resume  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError) as exc_info:
        async with provider.ephemeral():
            raise AssertionError("body must not start")

    assert exc_info.value is cancellation
    assert any("destruction also failed" in note for note in cancellation.__notes__)


async def test_ephemeral_destroy_retry_uses_state_returned_by_reconciliation() -> None:
    provider = _Provider()
    states: list[EnvironmentProviderResourceState] = []

    async def destroy(
        state: EnvironmentProviderResourceState,
        *,
        operation: EnvironmentOperationContext,
    ) -> None:
        provider._require_operation(operation, EnvironmentManagementAction.DESTROY, provider_key="test.provider")
        provider.destroy_operations.append(operation)
        states.append(state)
        if len(states) == 1:
            provider.reconciliation.append(
                EnvironmentReconciliationResult(
                    operation_id=operation.operation_id,
                    phase=EnvironmentReconciliationPhase.RUNNING,
                    state=_UPDATED_STATE,
                )
            )
            raise _unknown_error(operation)

    provider.destroy = destroy  # type: ignore[method-assign]

    async with provider.ephemeral():
        pass

    assert states == [_STATE, _UPDATED_STATE]
    assert [operation.attempt for operation in provider.destroy_operations] == [1, 2]


async def test_provider_operation_identity_cache_is_bounded() -> None:
    provider = _Provider()

    for index in range(provider._MAX_OBSERVED_OPERATIONS + 1):
        provider._require_operation(
            EnvironmentOperationContext(
                operation_id=f"operation-create-{index}",
                action=EnvironmentManagementAction.CREATE,
                resource_correlation=f"resource-{index}",
                attempt=1,
            ),
            EnvironmentManagementAction.CREATE,
            provider_key="test.provider",
        )

    assert len(provider._observed_operations) == provider._MAX_OBSERVED_OPERATIONS
    assert "operation-create-0" not in provider._observed_operations


async def test_ephemeral_rejects_reconciliation_for_another_operation() -> None:
    provider = _Provider(create_outcomes=["unknown"])

    original_create = provider.create

    async def create(*, operation: EnvironmentOperationContext) -> EnvironmentResource:
        provider.reconciliation.append(_result("operation-other", EnvironmentReconciliationPhase.ABSENT))
        return await original_create(operation=operation)

    provider.create = create  # type: ignore[method-assign]
    with pytest.raises(EnvironmentProviderError) as exc_info:
        async with provider.ephemeral():
            pass

    assert exc_info.value.code == "provider_failure"
