from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_harness import HarnessRunResult, HarnessState
from a13n_harness_ui.environment_runtime import EnvironmentFinalization
from a13n_harness_ui.live import HarnessUiSummaryHub, SummaryCursor
from a13n_harness_ui.notifications import reply_brief
from a13n_harness_ui.root_execution import RootContinuationSelection, RootRunOutcome
from a13n_harness_ui.root_run import RootRunCoordinator
from a13n_harness_ui.storage import ObjectRef
from anyio import fail_after
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.usage import RunUsage


def test_reply_brief_is_actual_prose_not_heading_code_or_markdown() -> None:
    assert (
        reply_brief(
            "# Done\n\n```python\nsecret = 1\n```\n\nFixed **the list**. See [tests](https://example.invalid).\n\nMore."
        )
        == "Fixed the list. See tests."
    )
    assert reply_brief("# Done\n\n```python\nunfinished") == ""
    assert reply_brief("已修复列表闪动。\n\n测试通过。") == "已修复列表闪动。"
    assert len(reply_brief("x" * 1000)) == 320


@pytest.mark.anyio
@pytest.mark.parametrize(
    "scenario", ["completed", "save_failed", "question", "approval", "preparation_failed", "cancelled"]
)
async def test_root_notices_follow_host_settlement_and_replay_without_new_history(scenario: str) -> None:
    hub = HarnessUiSummaryHub(epoch="notice-test")
    deferred = None
    if scenario == "question":
        deferred = DeferredToolRequests(
            calls=[
                ToolCallPart(
                    "ask_user_question",
                    {
                        "questions": [
                            {
                                "header": "Scope",
                                "question": "Which project should I update?",
                                "options": [
                                    {"label": "One", "description": "First project"},
                                    {"label": "Two", "description": "Second project"},
                                ],
                            }
                        ]
                    },
                    "question-1",
                )
            ]
        )
    if scenario == "approval":
        deferred = DeferredToolRequests(
            approvals=[ToolCallPart("shell_exec", {"command": "deploy"}, "approval-1")],
            metadata={"approval-1": {"reason": "This command deploys the application."}},
        )

    async def execute(**kwargs: Any) -> RootRunOutcome:
        if scenario == "preparation_failed":
            from a13n_harness_ui.errors import RunCoordinationError

            raise RunCoordinationError("Choose a model first.", code="model_missing")
        await kwargs["on_stream"](SimpleNamespace(run_id="run-notice"))
        status = "suspended" if deferred else "cancelled" if scenario == "cancelled" else "completed"
        return RootRunOutcome(
            result=HarnessRunResult(
                thread_id="thread_test",
                run_id="run-notice",
                status=status,
                state=HarnessState.new(thread_id="thread_test"),
                output="# Result\n\nFixed **project expansion** and archived filtering."
                if status == "completed"
                else None,
                suspend_reason="deferred" if deferred else None,
                deferred=deferred,
                usage=RunUsage(),
            ),
            environment=EnvironmentFinalization(state_publications=(), cleanup_errors=()),
            continuation=RootContinuationSelection(
                status="failed" if scenario == "save_failed" else "selected",
                error=OSError("disk unavailable") if scenario == "save_failed" else None,
            ),
            composition=cast(ObjectRef, SimpleNamespace(logical_digest="a" * 64)),
        )

    coordinator = RootRunCoordinator(cast(Any, SimpleNamespace(execute=execute)), summary_hub=hub)
    await coordinator.start()
    try:
        async with hub.subscribe() as subscription:
            cursor = subscription.cursor
            receipt = await coordinator.submit_prompt(thread_id="thread_test", prompt="Work")
            operation = await coordinator.wait(receipt.receipt_id)
            if scenario == "cancelled":
                assert operation.status == "cancelled"
            else:
                with fail_after(3):
                    while True:
                        event = await subscription.receive()
                        if event.notice is not None:
                            break
                assert event.kind == "root_operation"
                assert event.thread_id == "thread_test"
                assert event.notice.receipt_id == receipt.receipt_id
                assert event.notice.status == operation.status
                assert operation.completed_at is not None and operation.completed_at <= datetime.now(UTC)
                expected = {
                    "completed": "Fixed project expansion and archived filtering.",
                    "save_failed": "disk unavailable",
                    "question": "Which project should I update?",
                    "approval": "This command deploys the application.",
                    "preparation_failed": "Choose a model first.",
                }
                assert expected[scenario] in event.notice.brief
        # Joining without a cursor starts at the current boundary, not historical completions.
        async with hub.subscribe() as fresh:
            boundary = fresh.cursor.sequence
        events = []
        async with hub.subscribe(after=SummaryCursor(epoch=cursor.epoch, sequence=cursor.sequence)) as replay:
            with fail_after(3):
                for _ in range(boundary - cursor.sequence):
                    events.append(await replay.receive())
        notices = [event for event in events if event.notice is not None]
        assert len(notices) == (0 if scenario == "cancelled" else 1)
    finally:
        await coordinator.close(timeout_seconds=1)
        await hub.close()
