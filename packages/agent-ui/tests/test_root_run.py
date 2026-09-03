from __future__ import annotations

from typing import Any, cast

import pytest
from a13n_ui.root_run import RootRunCoordinator
from a13n_ui.surfaces import (
    ExternalToolResult,
    RootActivityState,
    RootOperationStatus,
    ThreadDeferredResponse,
)
from anyio import Event, sleep_forever

pytestmark = pytest.mark.anyio


class _PreparingExecutor:
    def __init__(self) -> None:
        self.started = Event()

    async def execute(self, **kwargs: object) -> None:
        del kwargs
        self.started.set()
        await sleep_forever()


class _Stream:
    run_id = "run-test"

    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _RunningExecutor:
    def __init__(self) -> None:
        self.started = Event()
        self.stream = _Stream()

    async def execute(self, **kwargs: object) -> None:
        on_stream = cast(Any, kwargs["on_stream"])
        await on_stream(self.stream)
        self.started.set()
        await sleep_forever()


class _ResponseExecutor:
    def __init__(self) -> None:
        self.started = Event()
        self.response: ThreadDeferredResponse | None = None

    async def execute(self, **kwargs: object) -> None:
        self.response = cast(ThreadDeferredResponse, kwargs["response"])
        self.started.set()
        await sleep_forever()


async def test_root_coordinator_cancels_preparation_and_stale_receipt_cannot_control_replacement() -> None:
    executor = _PreparingExecutor()
    coordinator = RootRunCoordinator(cast(Any, executor))
    await coordinator.start()
    try:
        first = await coordinator.submit_prompt(thread_id="thread-1", prompt="first")
        await executor.started.wait()
        activity = await coordinator.activity("thread-1")
        assert activity.state is RootActivityState.preparing
        assert activity.receipt_id == first.receipt_id

        assert (await coordinator.cancel(first.receipt_id)).accepted
        cancelled = await coordinator.wait(first.receipt_id)
        assert cancelled.status is RootOperationStatus.cancelled

        executor.started = Event()
        second = await coordinator.submit_prompt(thread_id="thread-1", prompt="second")
        await executor.started.wait()
        assert not (await coordinator.cancel(first.receipt_id)).accepted
        active = await coordinator.active("thread-1")
        assert active is not None
        assert active.receipt.receipt_id == second.receipt_id
        assert (await coordinator.cancel(second.receipt_id)).accepted
        assert (await coordinator.wait(second.receipt_id)).status is RootOperationStatus.cancelled
    finally:
        await coordinator.close(timeout_seconds=1)


async def test_root_coordinator_cancels_the_operation_scope_after_stream_creation() -> None:
    executor = _RunningExecutor()
    coordinator = RootRunCoordinator(cast(Any, executor))
    await coordinator.start()
    try:
        receipt = await coordinator.submit_prompt(thread_id="thread-1", prompt="run")
        await executor.started.wait()

        assert (await coordinator.cancel(receipt.receipt_id)).accepted
        operation = await coordinator.wait(receipt.receipt_id)

        assert executor.stream.cancelled
        assert operation.status is RootOperationStatus.cancelled
    finally:
        await coordinator.close(timeout_seconds=1)


async def test_root_response_admission_deep_copies_nested_payload() -> None:
    executor = _ResponseExecutor()
    coordinator = RootRunCoordinator(cast(Any, executor))
    await coordinator.start()
    response = ThreadDeferredResponse(
        expected_continuation_id="1" * 64,
        responses=(ExternalToolResult(request_id="request-1", result={"values": [1]}),),
    )
    try:
        receipt = await coordinator.submit_response(thread_id="thread-1", response=response)
        item = cast(ExternalToolResult, response.responses[0])
        payload = cast(dict[str, Any], item.result)
        cast(list[int], payload["values"]).append(2)
        await executor.started.wait()

        assert executor.response is not None
        captured = cast(ExternalToolResult, executor.response.responses[0])
        assert captured.result == {"values": [1]}
        assert (await coordinator.cancel(receipt.receipt_id)).accepted
        assert (await coordinator.wait(receipt.receipt_id)).status is RootOperationStatus.cancelled
    finally:
        await coordinator.close(timeout_seconds=1)


def test_root_response_rejects_oversized_nested_payload() -> None:
    with pytest.raises(ValueError, match="surface payload limit"):
        ThreadDeferredResponse(
            expected_continuation_id="1" * 64,
            responses=(ExternalToolResult(request_id="request-1", result="x" * (1024 * 1024)),),
        )
