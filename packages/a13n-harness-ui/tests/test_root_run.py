from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_harness_ui.errors import RunCoordinationError
from a13n_harness_ui.root_run import RootRunCoordinator
from a13n_harness_ui.storage import ObjectKind, ObjectRef
from a13n_harness_ui.surfaces import (
    ExternalToolResult,
    RootActivityState,
    RootOperationStatus,
    ThreadDeferredResponse,
)
from anyio import CancelScope, Event, fail_after, sleep, sleep_forever

pytestmark = pytest.mark.anyio


async def capture(**kwargs):
    return SimpleNamespace(
        prompt=kwargs["prompt"],
        response=kwargs["response"],
        published=SimpleNamespace(
            reference=ObjectRef(
                object_kind=ObjectKind.run_composition, object_schema_version="1", logical_digest="a" * 64
            )
        ),
    )


class _PreparingExecutor:
    capture = staticmethod(capture)

    def __init__(self) -> None:
        self.started = Event()

    async def execute(self, admission, **kwargs: object) -> None:
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
    capture = staticmethod(capture)

    def __init__(self) -> None:
        self.started = Event()
        self.stream = _Stream()

    async def execute(self, admission, **kwargs: object) -> None:
        on_stream = cast(Any, kwargs["on_stream"])
        await on_stream(self.stream)
        self.started.set()
        await sleep_forever()


class _ResponseExecutor:
    capture = staticmethod(capture)

    def __init__(self) -> None:
        self.started = Event()
        self.response: ThreadDeferredResponse | None = None

    async def execute(self, admission, **kwargs: object) -> None:
        self.response = admission.response
        self.started.set()
        await sleep_forever()


async def test_root_coordinator_cancels_preparation_and_stale_receipt_cannot_control_replacement() -> None:
    executor = _PreparingExecutor()
    coordinator = RootRunCoordinator(cast(Any, executor))
    await coordinator.start()
    try:
        assert await coordinator.active_count() == 0
        first = await coordinator.submit_prompt(thread_id="thread-1", prompt="first")
        await executor.started.wait()
        assert await coordinator.active_count() == 1
        activity = await coordinator.activity("thread-1")
        assert activity.state is RootActivityState.preparing
        assert activity.receipt_id == first.receipt_id

        assert (await coordinator.cancel(first.receipt_id)).accepted
        cancelled = await coordinator.wait(first.receipt_id)
        assert cancelled.status is RootOperationStatus.cancelled
        assert await coordinator.active_count() == 0
        latest = await coordinator.latest("thread-1")
        assert latest is not None
        assert latest.receipt.receipt_id == first.receipt_id
        assert (await coordinator.activities(("thread-1", "thread-2")))["thread-2"].state is RootActivityState.inactive

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


async def test_shutdown_cancellation_during_error_reporting_still_settles_receipt(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    executor = _PreparingExecutor()

    async def failing_execute(admission, **kwargs):
        executor.started.set()
        try:
            await sleep_forever()
        except BaseException:
            with CancelScope(shield=True):
                await sleep(0.05)
            raise RuntimeError("cleanup failed") from None

    monkeypatch.setattr(executor, "execute", failing_execute)
    coordinator = RootRunCoordinator(cast(Any, executor))
    await coordinator.start()
    receipt = await coordinator.submit_prompt(thread_id="thread-1", prompt="run")
    await executor.started.wait()
    await coordinator.close(timeout_seconds=0.01)
    with fail_after(1):
        operation = await coordinator.wait(receipt.receipt_id)
    assert operation.status is RootOperationStatus.failed
    assert operation.failure is not None
    assert "issues/new" in operation.failure.message
    assert await coordinator.active_count() == 0


async def test_usage_delivery_failure_retains_its_code_and_diagnostic_report(tmp_path, monkeypatch):
    from a13n_harness.usage import UsageReportError

    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    executor = _PreparingExecutor()

    async def failing_execute(admission, **kwargs):
        raise UsageReportError("Host usage delivery timed out.")

    monkeypatch.setattr(executor, "execute", failing_execute)
    coordinator = RootRunCoordinator(cast(Any, executor))
    await coordinator.start()
    try:
        receipt = await coordinator.submit_prompt(thread_id="thread-1", prompt="run")
        operation = await coordinator.wait(receipt.receipt_id)
        assert operation.status is RootOperationStatus.failed
        assert operation.failure.code == "usage_report_failed"
        assert operation.failure.message.startswith("Host usage delivery timed out.")
        assert "Diagnostic report:" in operation.failure.message
        assert list(tmp_path.glob("a13n-harness-ui-error-*.json"))
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


async def test_navigation_touch_is_explicit_and_never_disguises_accepted_steering(monkeypatch):
    from unittest.mock import AsyncMock

    touched = []
    fail_touch = False

    async def touch(thread_id):
        if fail_touch:
            raise RuntimeError("recency unavailable")
        touched.append(thread_id)

    executor = _RunningExecutor()
    steer = AsyncMock(return_value="enqueue-test")
    monkeypatch.setattr(executor.stream, "steer", steer, raising=False)
    coordinator = RootRunCoordinator(cast(Any, executor), touch_thread=touch)
    await coordinator.start()
    try:
        fail_touch = True
        with pytest.raises(RunCoordinationError, match="work was not admitted") as failure:
            await coordinator.submit_prompt(thread_id="thread-1", prompt="rejected", touch=True)
        assert failure.value.code == "thread_touch_failed"
        assert await coordinator.active_thread_ids() == ()
        fail_touch = False
        receipt = await coordinator.submit_prompt(thread_id="thread-1", prompt="human", touch=True)
        await executor.started.wait()
        assert touched == ["thread-1"]
        assert await coordinator.active_thread_ids() == ("thread-1",)
        with pytest.raises(RunCoordinationError, match="already has an active"):
            await coordinator.submit_prompt(thread_id="thread-1", prompt="duplicate", touch=True)
        assert touched == ["thread-1"]
        # Agent messages keep default touch=False even when accepted.
        assert (await coordinator.steer(receipt_id=receipt.receipt_id, message="background")).accepted
        assert touched == ["thread-1"]
        assert (await coordinator.steer(receipt_id=receipt.receipt_id, message="human steer", touch=True)).accepted
        assert touched == ["thread-1", "thread-1"]
        fail_touch = True
        result = await coordinator.steer(receipt_id=receipt.receipt_id, message="already enqueued", touch=True)
        assert result.accepted and result.enqueue_id == "enqueue-test"
        assert steer.await_count == 3
        await coordinator.cancel(receipt.receipt_id)
        await coordinator.wait(receipt.receipt_id)
        assert not (await coordinator.steer(receipt_id=receipt.receipt_id, message="stale", touch=True)).accepted
        assert await coordinator.active_thread_ids() == ()
        fail_touch = False
        receipt = await coordinator.submit_prompt(thread_id="thread-2", prompt="background")
        assert touched == ["thread-1", "thread-1"]
        await coordinator.cancel(receipt.receipt_id)
        await coordinator.wait(receipt.receipt_id)
    finally:
        await coordinator.close(timeout_seconds=1)


@pytest.mark.parametrize("capture_fails", [False, True])
async def test_root_snapshots_do_not_wait_for_unrelated_admission(capture_fails, monkeypatch) -> None:
    from anyio import create_task_group

    executor = _RunningExecutor()
    coordinator = RootRunCoordinator(cast(Any, executor))
    await coordinator.start()
    entered, release, finished = Event(), Event(), Event()
    try:
        terminal = await coordinator.submit_prompt(thread_id="terminal", prompt="first")
        await executor.started.wait()
        assert (await coordinator.cancel(terminal.receipt_id)).accepted
        await coordinator.wait(terminal.receipt_id)
        executor.started = Event()
        running = await coordinator.submit_prompt(thread_id="running", prompt="second")
        await executor.started.wait()

        async def slow_capture(**kwargs):
            entered.set()
            await release.wait()
            if capture_fails:
                raise RunCoordinationError("Rejected capture", code="capture_failed")
            return await capture(**kwargs)

        monkeypatch.setattr(executor, "capture", slow_capture)

        async def submit():
            try:
                await coordinator.submit_prompt(thread_id="slow", prompt="third")
            except RunCoordinationError as error:
                assert capture_fails and error.code == "capture_failed"
            finally:
                finished.set()

        async with create_task_group() as tasks:
            tasks.start_soon(submit)
            await entered.wait()
            try:
                with fail_after(1):
                    assert await coordinator.active_count() == 1
                    assert await coordinator.active_thread_ids() == ("running",)
                    assert (await coordinator.get(running.receipt_id)).status is RootOperationStatus.running
                    assert (await coordinator.active("running")).receipt == running
                    assert await coordinator.active("slow") is None
                    states = await coordinator.activities(("running", "slow"))
                    assert states["running"].state is RootActivityState.running
                    assert states["slow"].state is RootActivityState.inactive
                    assert (await coordinator.latest("terminal")).receipt == terminal
                    assert set(await coordinator.latest_many(("terminal", "running"))) == {"terminal"}
                    assert await coordinator.interaction_expiry("slow", "a" * 64) is None
                    assert (await coordinator.wait(running.receipt_id, timeout_seconds=0)).receipt == running
                assert not finished.is_set()
            finally:
                release.set()
            await finished.wait()
        assert (await coordinator.active("slow") is None) is capture_fails
    finally:
        release.set()
        await coordinator.close(timeout_seconds=1)


@pytest.mark.parametrize("running", [False, True])
async def test_root_cancellation_settles_during_unrelated_admission(running, monkeypatch) -> None:
    from anyio import create_task_group

    executor = _RunningExecutor() if running else _PreparingExecutor()
    coordinator = RootRunCoordinator(cast(Any, executor))
    entered, release = Event(), Event()
    await coordinator.start()
    try:
        receipt = await coordinator.submit_prompt(thread_id="active", prompt="first")
        await executor.started.wait()

        async def slow_capture(**kwargs):
            entered.set()
            await release.wait()
            return await capture(**kwargs)

        monkeypatch.setattr(executor, "capture", slow_capture)

        async def submit():
            await coordinator.submit_prompt(thread_id="slow", prompt="second")

        async with create_task_group() as tasks:
            tasks.start_soon(submit)
            await entered.wait()
            try:
                with fail_after(1):
                    assert await coordinator.composition_reference(receipt.receipt_id) is not None
                    if running:
                        from unittest.mock import AsyncMock

                        executor.stream.steer = AsyncMock(return_value="enqueue-test")
                        steered = await coordinator.steer(receipt_id=receipt.receipt_id, message="follow up")
                        assert steered.accepted and steered.enqueue_id == "enqueue-test"
                    assert (await coordinator.cancel(receipt.receipt_id)).accepted
                    assert (await coordinator.wait(receipt.receipt_id)).status is RootOperationStatus.cancelled
                    assert await coordinator.active("active") is None
                    assert not (await coordinator.cancel(receipt.receipt_id)).accepted
                assert not release.is_set()
            finally:
                release.set()
    finally:
        release.set()
        await coordinator.close(timeout_seconds=1)


async def test_root_running_transition_and_failure_settle_during_unrelated_admission(monkeypatch) -> None:
    from anyio import create_task_group

    executor = _RunningExecutor()
    coordinator = RootRunCoordinator(cast(Any, executor))
    entered, release, execute = Event(), Event(), Event()

    async def fail_after_stream(admission, **kwargs):
        await execute.wait()
        await kwargs["on_stream"](executor.stream)
        executor.started.set()
        raise RunCoordinationError("Execution failed", code="execution_failed")

    monkeypatch.setattr(executor, "execute", fail_after_stream)
    await coordinator.start()
    try:
        receipt = await coordinator.submit_prompt(thread_id="active", prompt="first")

        async def slow_capture(**kwargs):
            entered.set()
            await release.wait()
            return await capture(**kwargs)

        monkeypatch.setattr(executor, "capture", slow_capture)

        async def submit():
            await coordinator.submit_prompt(thread_id="slow", prompt="second")

        async with create_task_group() as tasks:
            tasks.start_soon(submit)
            await entered.wait()
            try:
                execute.set()
                with fail_after(1):
                    await executor.started.wait()
                    result = await coordinator.wait(receipt.receipt_id)
                    assert result.status is RootOperationStatus.failed
                    assert result.failure.code == "execution_failed"
                assert not release.is_set()
            finally:
                release.set()
    finally:
        release.set()
        await coordinator.close(timeout_seconds=1)


async def test_idle_mutations_and_shutdown_keep_their_admission_fence() -> None:
    from anyio import create_task_group, wait_all_tasks_blocked

    executor = _PreparingExecutor()
    coordinator = RootRunCoordinator(cast(Any, executor))
    await coordinator.start()
    submitted = Event()

    async def submit():
        await coordinator.submit_prompt(thread_id="one", prompt="queued")
        submitted.set()

    try:
        async with create_task_group() as tasks:
            async with coordinator.require_inactive("one"):
                tasks.start_soon(submit)
                await wait_all_tasks_blocked()
                assert not submitted.is_set()
                # Read-only observation is not authority to bypass the fence.
                assert await coordinator.active("one") is None
            await submitted.wait()
        with pytest.raises(RunCoordinationError, match="active root"):
            async with coordinator.require_inactive("one"):
                pytest.fail("admitted operation must exclude idle-only mutation")
        await coordinator.stop_admission()
        with pytest.raises(RunCoordinationError) as error:
            await coordinator.submit_prompt(thread_id="two", prompt="rejected")
        assert error.value.code == "app_stopping"
    finally:
        await coordinator.close(timeout_seconds=1)


@pytest.mark.parametrize("capture_fails", [False, True])
async def test_root_admission_does_not_serialize_different_threads(capture_fails, monkeypatch):
    from anyio import create_task_group, wait_all_tasks_blocked

    executor = _PreparingExecutor()
    entered, release, other_admitted = Event(), Event(), Event()

    async def slow_capture(**kwargs):
        if kwargs["thread_id"] == "slow":
            entered.set()
            await release.wait()
            if capture_fails:
                raise RunCoordinationError("Rejected capture", code="capture_failed")
        return await capture(**kwargs)

    monkeypatch.setattr(executor, "capture", slow_capture)
    coordinator = RootRunCoordinator(cast(Any, executor))
    await coordinator.start()

    async def submit(thread_id):
        try:
            await coordinator.submit_prompt(thread_id=thread_id, prompt="run")
        except RunCoordinationError as error:
            assert thread_id == "slow" and capture_fails and error.code == "capture_failed"
        if thread_id == "other":
            other_admitted.set()

    try:
        async with create_task_group() as tasks:
            tasks.start_soon(submit, "slow")
            await entered.wait()
            tasks.start_soon(submit, "other")
            try:
                await wait_all_tasks_blocked()
                assert other_admitted.is_set()
                assert await coordinator.active("slow") is None
            finally:
                release.set()
    finally:
        release.set()
        await coordinator.close(timeout_seconds=1)


@pytest.mark.parametrize("holder", ["capture", "mutation"])
async def test_admission_and_idle_mutation_fence_only_the_same_thread(holder, monkeypatch):
    from anyio import create_task_group, wait_all_tasks_blocked

    executor = _PreparingExecutor()
    entered, release, contender_done, other_done = Event(), Event(), Event(), Event()

    async def slow_capture(**kwargs):
        if holder == "capture" and kwargs["thread_id"] == "same":
            entered.set()
            await release.wait()
        return await capture(**kwargs)

    monkeypatch.setattr(executor, "capture", slow_capture)
    coordinator = RootRunCoordinator(cast(Any, executor))
    await coordinator.start()

    async def hold():
        if holder == "capture":
            await coordinator.submit_prompt(thread_id="same", prompt="run")
        else:
            async with coordinator.require_inactive("same"):
                entered.set()
                await release.wait()

    async def contend():
        if holder == "capture":
            with pytest.raises(RunCoordinationError) as error:
                async with coordinator.require_inactive("same"):
                    pytest.fail("capture must fence idle mutation")
            assert error.value.code == "thread_run_active"
        else:
            await coordinator.submit_prompt(thread_id="same", prompt="run")
        contender_done.set()

    async def other():
        async with coordinator.require_inactive("other"):
            pass
        await coordinator.submit_prompt(thread_id="other", prompt="run")
        other_done.set()

    try:
        async with create_task_group() as tasks:
            tasks.start_soon(hold)
            await entered.wait()
            tasks.start_soon(contend)
            tasks.start_soon(other)
            try:
                await wait_all_tasks_blocked()
                assert not contender_done.is_set()
                assert other_done.is_set()
            finally:
                release.set()
        assert contender_done.is_set()
    finally:
        release.set()
        await coordinator.close(timeout_seconds=1)


async def test_cancelled_fence_waiters_do_not_split_lock_or_leak_entries():
    from anyio import create_task_group, wait_all_tasks_blocked

    coordinator = RootRunCoordinator(cast(Any, _PreparingExecutor()))
    await coordinator.start()
    scopes = {}
    entered = []
    release = Event()

    async def mutate(name):
        with CancelScope() as scope:
            scopes[name] = scope
            async with coordinator.require_inactive("same"):
                entered.append(name)
                await release.wait()
                if name == "second":
                    raise ValueError("mutation failed")

    async def failing_mutation():
        with pytest.raises(ValueError, match="mutation failed"):
            await mutate("second")

    try:
        async with create_task_group() as tasks:
            async with coordinator.require_inactive("same"):
                tasks.start_soon(mutate, "cancelled")
                tasks.start_soon(failing_mutation)
                await wait_all_tasks_blocked()
                scopes["cancelled"].cancel()
                await wait_all_tasks_blocked()
                tasks.start_soon(mutate, "third")
                await wait_all_tasks_blocked()
                assert entered == []
            try:
                await wait_all_tasks_blocked()
                assert entered == ["second"]
            finally:
                release.set()
        assert entered == ["second", "third"]
        assert coordinator._thread_fences == {}
        assert coordinator._fences_idle.is_set()
    finally:
        release.set()
        await coordinator.close(timeout_seconds=1)


@pytest.mark.parametrize("capture_fails", [False, True])
@pytest.mark.parametrize("shutdown", ["stop", "close"])
async def test_shutdown_seals_and_joins_all_inflight_captures(capture_fails, shutdown, monkeypatch):
    from anyio import create_task_group, wait_all_tasks_blocked

    executor = _PreparingExecutor()
    ready, stopping, stopped = Event(), Event(), Event()
    entered = {name: Event() for name in ("one", "two")}
    release = {name: Event() for name in entered}
    receipts, errors = {}, {}

    async def slow_capture(**kwargs):
        name = kwargs["thread_id"]
        entered[name].set()
        await release[name].wait()
        if capture_fails and name == "one":
            raise RunCoordinationError("Rejected capture", code="capture_failed")
        return await capture(**kwargs)

    monkeypatch.setattr(executor, "capture", slow_capture)
    coordinator = RootRunCoordinator(cast(Any, executor))

    async def own_coordinator():
        await coordinator.start()
        ready.set()
        await stopping.wait()
        if shutdown == "stop":
            await coordinator.stop_admission()
            stopped.set()
        await coordinator.close(timeout_seconds=1)
        stopped.set()

    async def submit(name):
        try:
            receipts[name] = await coordinator.submit_prompt(thread_id=name, prompt="run")
        except RunCoordinationError as error:
            errors[name] = error.code

    async with create_task_group() as tasks:
        tasks.start_soon(own_coordinator)
        await ready.wait()
        tasks.start_soon(submit, "one")
        tasks.start_soon(submit, "two")
        try:
            await wait_all_tasks_blocked()
            assert all(event.is_set() for event in entered.values())
            # This waiter was registered before stopping, but cannot capture afterward.
            tasks.start_soon(submit, "one")
            await wait_all_tasks_blocked()
            stopping.set()
            await wait_all_tasks_blocked()
            assert not coordinator._accepting
            assert not stopped.is_set()
            with pytest.raises(RunCoordinationError) as error:
                await coordinator.submit_prompt(thread_id="new", prompt="rejected")
            assert error.value.code == "app_stopping"
            with pytest.raises(RunCoordinationError) as error:
                async with coordinator.require_inactive("new"):
                    pytest.fail("new mutations must not enter after stopping")
            assert error.value.code == "app_stopping"
            release["one"].set()
            await wait_all_tasks_blocked()
            assert not stopped.is_set()
        finally:
            stopping.set()
            for event in release.values():
                event.set()
    assert stopped.is_set()
    assert "two" in receipts
    assert ("one" in receipts) is not capture_fails
    assert errors["one"] == "app_stopping"
    assert await coordinator.active_count() == 0
    assert coordinator._thread_fences == {}
    for receipt in receipts.values():
        assert (await coordinator.get(receipt.receipt_id)).status is RootOperationStatus.cancelled


async def test_caller_cancellation_during_capture_still_admits_and_drains(monkeypatch):
    from anyio import create_task_group, wait_all_tasks_blocked

    executor = _PreparingExecutor()
    entered, release, finished = Event(), Event(), Event()
    scopes, receipts = [], []

    async def slow_capture(**kwargs):
        entered.set()
        await release.wait()
        return await capture(**kwargs)

    monkeypatch.setattr(executor, "capture", slow_capture)
    coordinator = RootRunCoordinator(cast(Any, executor))
    await coordinator.start()

    async def submit():
        with CancelScope() as scope:
            scopes.append(scope)
            receipts.append(await coordinator.submit_prompt(thread_id="one", prompt="run"))
        finished.set()

    try:
        async with create_task_group() as tasks:
            tasks.start_soon(submit)
            await entered.wait()
            scopes[0].cancel()
            await wait_all_tasks_blocked()
            assert not finished.is_set()
            release.set()
        assert receipts
        assert await coordinator.active("one") is not None
        assert coordinator._thread_fences == {}
    finally:
        release.set()
        await coordinator.close(timeout_seconds=1)
