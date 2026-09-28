"""WebUI deadlines belong to the App, not connected browser participants."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_harness import HarnessRunResult, HarnessState
from a13n_harness.usage import RunUsageSummary
from a13n_harness_ui.environment_runtime import EnvironmentFinalization
from a13n_harness_ui.errors import RunCoordinationError
from a13n_harness_ui.interaction_timeout import QUESTION_TIMEOUT_MESSAGE
from a13n_harness_ui.root_execution import RootContinuationSelection, RootRunOutcome
from a13n_harness_ui.root_run import RootRunCoordinator
from a13n_harness_ui.storage import ObjectKind, ObjectRef
from a13n_harness_ui.surfaces import ExternalToolResult, RootOperationStatus, ThreadDeferredResponse
from anyio import Event, create_task_group, fail_after
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ToolCallPart

from .test_root_run import capture

pytestmark = pytest.mark.anyio
THREAD = "thread_timeout"
CONTINUATION = "1" * 64


def suspended_outcome(*, seconds=120, selected=True):
    requests = DeferredToolRequests(
        calls=[
            ToolCallPart("ask_user_question", {}, "question"),
            ToolCallPart("external", {}, "external"),
        ],
        approvals=[ToolCallPart("shell_exec", {}, "approval")],
    )
    return RootRunOutcome(
        result=HarnessRunResult(
            thread_id=THREAD,
            run_id="run_timeout",
            status="suspended",
            output=None,
            state=HarnessState(schema_version="1", thread_id=THREAD),
            usage=RunUsageSummary(),
            suspend_reason="deferred",
            deferred=requests,
        ),
        environment=EnvironmentFinalization(cleanup_errors=(), state_publications=()),
        continuation=RootContinuationSelection(
            status="selected" if selected else "failed",
            reference=ObjectRef(
                object_kind=ObjectKind.continuation, object_schema_version="1", logical_digest=CONTINUATION
            ),
        ),
        composition=ObjectRef(
            object_kind=ObjectKind.run_composition, object_schema_version="1", logical_digest="2" * 64
        ),
        interaction_timeout_seconds=seconds,
    )


def completed_outcome():
    return replace(
        suspended_outcome(),
        result=HarnessRunResult(
            thread_id=THREAD,
            run_id="run_resumed",
            status="completed",
            output="continued",
            state=HarnessState(schema_version="1", thread_id=THREAD),
            usage=RunUsageSummary(),
        ),
        continuation=RootContinuationSelection(
            status="selected",
            reference=ObjectRef(
                object_kind=ObjectKind.continuation, object_schema_version="1", logical_digest="3" * 64
            ),
        ),
    )


def answer():
    return ThreadDeferredResponse(
        expected_continuation_id=CONTINUATION,
        responses=(ExternalToolResult(request_id="question", result="human"),),
    )


async def test_timeout_denies_complete_mixed_batch_once_without_a_viewer(monkeypatch):
    seen = []
    timer_started, expire, timer_finished = Event(), Event(), Event()

    async def execute(admission, **kwargs):
        seen.append(admission.response)
        return suspended_outcome() if admission.response is None else completed_outcome()

    coordinator = RootRunCoordinator(
        cast(Any, SimpleNamespace(capture=capture, execute=execute)), interaction_timeouts=True
    )
    original_expire = coordinator._expire_interaction

    async def controlled_expiry(thread_id, pending):
        timer_started.set()
        await expire.wait()
        with monkeypatch.context() as clock:
            clock.setattr("a13n_harness_ui.root_run.monotonic", lambda: pending.deadline + 1)
            await original_expire(thread_id, pending)
        timer_finished.set()

    monkeypatch.setattr(coordinator, "_expire_interaction", controlled_expiry)
    await coordinator.start()
    try:
        receipt = await coordinator.submit_prompt(thread_id=THREAD, prompt="ask")
        assert (await coordinator.wait(receipt.receipt_id)).status is RootOperationStatus.suspended
        with fail_after(5):
            await timer_started.wait()  # Admission really scheduled the automatic timer.
            assert await coordinator.interaction_expiry(THREAD, CONTINUATION) is not None
            expire.set()
            await timer_finished.wait()
            latest = await coordinator.active(THREAD) or await coordinator.latest(THREAD)
            assert latest is not None and latest.receipt.receipt_id != receipt.receipt_id
            assert (await coordinator.wait(latest.receipt.receipt_id)).status is RootOperationStatus.completed
        assert len(seen) == 2
        response = seen[1]
        assert response.expected_continuation_id == CONTINUATION
        question, external, approval = response.responses
        assert question.denied and question.denial_message == QUESTION_TIMEOUT_MESSAGE
        assert external.denied and not approval.approved
        assert await coordinator.interaction_expiry(THREAD, CONTINUATION) is None
    finally:
        await coordinator.close(timeout_seconds=1)


@pytest.mark.parametrize("human_first", [True, False])
async def test_human_and_timeout_share_exact_admission_fence(monkeypatch, human_first):
    entered, finish = Event(), Event()
    responses = []

    async def execute(admission, **kwargs):
        if admission.response is None:
            return suspended_outcome()
        responses.append(admission.response)
        entered.set()
        await finish.wait()
        return completed_outcome()

    coordinator = RootRunCoordinator(
        cast(Any, SimpleNamespace(capture=capture, execute=execute)), interaction_timeouts=True
    )
    await coordinator.start()
    try:
        receipt = await coordinator.submit_prompt(thread_id=THREAD, prompt="ask")
        await coordinator.wait(receipt.receipt_id)
        pending = coordinator._interaction_waits[THREAD]
        if human_first:
            await coordinator.submit_response(thread_id=THREAD, response=answer())
        monkeypatch.setattr("a13n_harness_ui.root_run.monotonic", lambda: pending.deadline + 1)
        if not human_first:
            # Late input loses even before the timeout task gets CPU time.
            with pytest.raises(RunCoordinationError, match="deadline"):
                await coordinator.submit_response(thread_id=THREAD, response=answer())
        async with create_task_group() as tasks:
            tasks.start_soon(coordinator._expire_interaction, THREAD, pending)
            tasks.start_soon(coordinator._expire_interaction, THREAD, pending)
        await entered.wait()
        assert len(responses) == 1
        assert responses[0].responses[0].denied is not human_first
        assert await coordinator.interaction_expiry(THREAD, CONTINUATION) is None
        finish.set()
    finally:
        finish.set()
        await coordinator.close(timeout_seconds=1)


@pytest.mark.parametrize("action", ["shutdown", "archive", "answer"])
async def test_disarming_wait_wakes_timer_without_resubmission(action):
    responses = []

    async def execute(admission, **kwargs):
        if admission.response is None:
            return suspended_outcome()
        responses.append(admission.response)
        # Preparation failure does not authorize an automatic retry.
        raise RunCoordinationError("Invalid response", code="thread_deferred_response_incomplete")

    coordinator = RootRunCoordinator(
        cast(Any, SimpleNamespace(capture=capture, execute=execute)), interaction_timeouts=True
    )
    await coordinator.start()
    try:
        receipt = await coordinator.submit_prompt(thread_id=THREAD, prompt="ask")
        await coordinator.wait(receipt.receipt_id)
        pending = coordinator._interaction_waits[THREAD]
        if action == "shutdown":
            await coordinator.stop_admission()
        elif action == "archive":
            async with coordinator.require_inactive(THREAD):
                pass
        else:
            submitted = await coordinator.submit_response(thread_id=THREAD, response=answer())
            assert (await coordinator.wait(submitted.receipt_id)).status is RootOperationStatus.failed
        assert pending.cancelled.is_set()
        assert await coordinator.interaction_expiry(THREAD, CONTINUATION) is None
        with fail_after(1):
            await coordinator._expire_interaction(THREAD, pending)
        assert len(responses) == (1 if action == "answer" else 0)
    finally:
        await coordinator.close(timeout_seconds=0.5)


@pytest.mark.parametrize("enabled,selected", [(False, True), (True, False)])
async def test_no_deadline_for_cli_or_unsaved_suspension(enabled, selected):
    async def execute(admission, **kwargs):
        return suspended_outcome(selected=selected)

    coordinator = RootRunCoordinator(
        cast(Any, SimpleNamespace(capture=capture, execute=execute)), interaction_timeouts=enabled
    )
    await coordinator.start()
    try:
        receipt = await coordinator.submit_prompt(thread_id=THREAD, prompt="ask")
        await coordinator.wait(receipt.receipt_id)
        assert await coordinator.interaction_expiry(THREAD, CONTINUATION) is None
    finally:
        await coordinator.close(timeout_seconds=1)


@pytest.mark.parametrize("human", [False, True])
async def test_unrelated_capture_does_not_block_interaction_admission(human, monkeypatch):
    from anyio import wait_all_tasks_blocked

    entered, release, answered = Event(), Event(), Event()
    responses = []

    async def controlled_capture(**kwargs):
        if kwargs["thread_id"] == "unrelated":
            entered.set()
            await release.wait()
        return await capture(**kwargs)

    async def execute(admission, **kwargs):
        if admission.response is not None:
            responses.append(admission.response)
            return completed_outcome()
        return suspended_outcome()

    coordinator = RootRunCoordinator(
        cast(Any, SimpleNamespace(capture=controlled_capture, execute=execute)), interaction_timeouts=True
    )
    await coordinator.start()

    async def unrelated():
        await coordinator.submit_prompt(thread_id="unrelated", prompt="slow")

    async def respond(pending):
        if human:
            await coordinator.submit_response(thread_id=THREAD, response=answer())
        else:
            await coordinator._expire_interaction(THREAD, pending)
        answered.set()

    try:
        receipt = await coordinator.submit_prompt(thread_id=THREAD, prompt="ask")
        await coordinator.wait(receipt.receipt_id)
        pending = coordinator._interaction_waits[THREAD]
        if not human:
            monkeypatch.setattr("a13n_harness_ui.root_run.monotonic", lambda: pending.deadline + 1)
        async with create_task_group() as tasks:
            tasks.start_soon(unrelated)
            await entered.wait()
            tasks.start_soon(respond, pending)
            try:
                # Expiry first crosses its zero-deadline CancelScope; wait for
                # that scheduled cancellation, not just an idle-loop snapshot.
                with fail_after(1):
                    await answered.wait()
                await wait_all_tasks_blocked()
                assert len(responses) == 1
                assert responses[0].responses[0].denied is not human
                assert pending.cancelled.is_set()
            finally:
                release.set()
    finally:
        release.set()
        await coordinator.close(timeout_seconds=1)


@pytest.mark.parametrize("capture_fails", [False, True])
async def test_stop_drains_human_capture_before_disarming_its_pending_wait(capture_fails, monkeypatch):
    from anyio import wait_all_tasks_blocked

    entered, release, stopped = Event(), Event(), Event()
    responses = []

    async def controlled_capture(**kwargs):
        if kwargs["response"] is not None:
            entered.set()
            await release.wait()
            if capture_fails:
                raise RunCoordinationError("Rejected response", code="capture_failed")
        return await capture(**kwargs)

    async def execute(admission, **kwargs):
        if admission.response is not None:
            responses.append(admission.response)
            return completed_outcome()
        return suspended_outcome()

    coordinator = RootRunCoordinator(
        cast(Any, SimpleNamespace(capture=controlled_capture, execute=execute)), interaction_timeouts=True
    )
    await coordinator.start()

    async def respond():
        if capture_fails:
            with pytest.raises(RunCoordinationError, match="Rejected response"):
                await coordinator.submit_response(thread_id=THREAD, response=answer())
        else:
            await coordinator.submit_response(thread_id=THREAD, response=answer())

    async def stop():
        await coordinator.stop_admission()
        stopped.set()

    try:
        receipt = await coordinator.submit_prompt(thread_id=THREAD, prompt="ask")
        await coordinator.wait(receipt.receipt_id)
        pending = coordinator._interaction_waits[THREAD]
        async with create_task_group() as tasks:
            tasks.start_soon(respond)
            await entered.wait()
            # Passing the admission fence before expiry admits this response even
            # when its shielded capture completes after the deadline and stop.
            monkeypatch.setattr("a13n_harness_ui.root_run.monotonic", lambda: pending.deadline + 1)
            tasks.start_soon(coordinator._expire_interaction, THREAD, pending)
            tasks.start_soon(stop)
            try:
                await wait_all_tasks_blocked()
                assert not stopped.is_set()
                assert coordinator._interaction_waits[THREAD] is pending
                assert not pending.cancelled.is_set()
            finally:
                release.set()
        assert stopped.is_set()
        assert pending.cancelled.is_set()
        assert THREAD not in coordinator._interaction_waits
        assert len(responses) == (0 if capture_fails else 1)
        assert coordinator._thread_fences == {}
    finally:
        release.set()
        await coordinator.close(timeout_seconds=1)


async def test_human_deadline_is_checked_after_waiting_for_its_thread_fence(monkeypatch):
    from anyio import wait_all_tasks_blocked

    responses = []

    async def execute(admission, **kwargs):
        if admission.response is not None:
            responses.append(admission.response)
        return suspended_outcome()

    coordinator = RootRunCoordinator(
        cast(Any, SimpleNamespace(capture=capture, execute=execute)), interaction_timeouts=True
    )
    await coordinator.start()
    errors = []

    async def respond():
        try:
            await coordinator.submit_response(thread_id=THREAD, response=answer())
        except RunCoordinationError as error:
            errors.append(error.code)

    try:
        receipt = await coordinator.submit_prompt(thread_id=THREAD, prompt="ask")
        await coordinator.wait(receipt.receipt_id)
        pending = coordinator._interaction_waits[THREAD]
        async with create_task_group() as tasks:
            # Failed idle mutation leaves the pending interaction intact.
            with pytest.raises(ValueError, match="mutation failed"):
                async with coordinator.require_inactive(THREAD):
                    tasks.start_soon(respond)
                    await wait_all_tasks_blocked()
                    assert errors == []
                    monkeypatch.setattr("a13n_harness_ui.root_run.monotonic", lambda: pending.deadline + 1)
                    raise ValueError("mutation failed")
        assert errors == ["thread_interaction_expired"]
        assert responses == []
        assert coordinator._interaction_waits[THREAD] is pending
        assert not pending.cancelled.is_set()
        assert coordinator._thread_fences == {}
    finally:
        await coordinator.close(timeout_seconds=1)


async def test_failed_response_capture_preserves_pending_interaction():
    async def controlled_capture(**kwargs):
        if kwargs["response"] is not None:
            raise RunCoordinationError("Rejected response", code="capture_failed")
        return await capture(**kwargs)

    async def execute(admission, **kwargs):
        return suspended_outcome()

    coordinator = RootRunCoordinator(
        cast(Any, SimpleNamespace(capture=controlled_capture, execute=execute)), interaction_timeouts=True
    )
    await coordinator.start()
    try:
        receipt = await coordinator.submit_prompt(thread_id=THREAD, prompt="ask")
        await coordinator.wait(receipt.receipt_id)
        pending = coordinator._interaction_waits[THREAD]
        with pytest.raises(RunCoordinationError, match="Rejected response"):
            await coordinator.submit_response(thread_id=THREAD, response=answer())
        assert coordinator._interaction_waits[THREAD] is pending
        assert not pending.cancelled.is_set()
        assert coordinator._thread_fences == {}
    finally:
        await coordinator.close(timeout_seconds=1)
