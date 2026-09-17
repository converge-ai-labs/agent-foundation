"""WebUI deadlines belong to the App, not connected browser participants."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_harness import HarnessRunResult, HarnessState
from a13n_harness_ui.environment_runtime import EnvironmentFinalization
from a13n_harness_ui.errors import RunCoordinationError
from a13n_harness_ui.interaction_timeout import QUESTION_TIMEOUT_MESSAGE
from a13n_harness_ui.root_execution import RootContinuationSelection, RootRunOutcome
from a13n_harness_ui.root_run import RootRunCoordinator
from a13n_harness_ui.storage import ObjectKind, ObjectRef
from a13n_harness_ui.surfaces import ExternalToolResult, RootOperationStatus, ThreadDeferredResponse
from anyio import Event, create_task_group, fail_after, sleep
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.usage import RunUsage

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
            usage=RunUsage(),
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
            usage=RunUsage(),
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


async def test_timeout_denies_complete_mixed_batch_once_without_a_viewer():
    seen = []

    async def execute(**kwargs):
        seen.append(kwargs["response"])
        return suspended_outcome(seconds=0.02) if kwargs["response"] is None else completed_outcome()

    coordinator = RootRunCoordinator(cast(Any, SimpleNamespace(execute=execute)), interaction_timeouts=True)
    await coordinator.start()
    try:
        receipt = await coordinator.submit_prompt(thread_id=THREAD, prompt="ask")
        assert (await coordinator.wait(receipt.receipt_id)).status is RootOperationStatus.suspended
        assert await coordinator.interaction_expiry(THREAD, CONTINUATION) is not None
        with fail_after(2):
            while len(seen) < 2 or await coordinator.active(THREAD) is not None:
                await sleep(0.005)
        assert len(seen) == 2
        response = seen[1]
        assert response.expected_continuation_id == CONTINUATION
        question, external, approval = response.responses
        assert question.denied and question.denial_message == QUESTION_TIMEOUT_MESSAGE
        assert external.denied and not approval.approved
        assert await coordinator.interaction_expiry(THREAD, CONTINUATION) is None
        await sleep(0.03)
        assert len(seen) == 2
    finally:
        await coordinator.close(timeout_seconds=1)


@pytest.mark.parametrize("human_first", [True, False])
async def test_human_and_timeout_share_exact_admission_fence(monkeypatch, human_first):
    entered, finish = Event(), Event()
    responses = []

    async def execute(**kwargs):
        if kwargs["response"] is None:
            return suspended_outcome()
        responses.append(kwargs["response"])
        entered.set()
        await finish.wait()
        return completed_outcome()

    coordinator = RootRunCoordinator(cast(Any, SimpleNamespace(execute=execute)), interaction_timeouts=True)
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

    async def execute(**kwargs):
        if kwargs["response"] is None:
            return suspended_outcome()
        responses.append(kwargs["response"])
        # Preparation failure does not authorize an automatic retry.
        raise RunCoordinationError("Invalid response", code="thread_deferred_response_incomplete")

    coordinator = RootRunCoordinator(cast(Any, SimpleNamespace(execute=execute)), interaction_timeouts=True)
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
    async def execute(**kwargs):
        return suspended_outcome(selected=selected)

    coordinator = RootRunCoordinator(cast(Any, SimpleNamespace(execute=execute)), interaction_timeouts=enabled)
    await coordinator.start()
    try:
        receipt = await coordinator.submit_prompt(thread_id=THREAD, prompt="ask")
        await coordinator.wait(receipt.receipt_id)
        assert await coordinator.interaction_expiry(THREAD, CONTINUATION) is None
    finally:
        await coordinator.close(timeout_seconds=1)
