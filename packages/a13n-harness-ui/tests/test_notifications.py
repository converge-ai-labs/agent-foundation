from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_harness import HarnessRunResult, HarnessState
from a13n_harness.usage import RunUsageSummary
from a13n_harness_ui.environment_runtime import EnvironmentFinalization
from a13n_harness_ui.live import HarnessUiSummaryHub, RootOperationNotice, SummaryCursor
from a13n_harness_ui.notifications import reply_brief
from a13n_harness_ui.root_execution import RootContinuationSelection, RootRunOutcome
from a13n_harness_ui.root_run import RootRunCoordinator
from a13n_harness_ui.storage import ObjectRef
from anyio import Event, fail_after
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import DeferredToolRequests


def test_reply_brief_is_actual_prose_not_heading_code_or_markdown() -> None:
    assert (
        reply_brief(
            "# Done\n\n```python\nsecret = 1\n```\n\nFixed **the list**. See [tests](https://example.invalid).\n\nMore."
        )
        == "Fixed the list. See tests. More."
    )
    assert reply_brief("# Done\n\n```python\nunfinished") == ""
    assert reply_brief("已修复列表闪动。\n\n测试通过。") == "已修复列表闪动。 测试通过。"
    assert len(reply_brief("x" * 1000)) == 320


def test_reply_brief_includes_details_after_a_short_introduction() -> None:
    assert (
        reply_brief(
            "已同步最新代码\N{FULLWIDTH COLON}\n\n- `main` 已快进更新至 `origin/main`。\n- 工作区**干净**。\n\n未运行测试。"
        )
        == "已同步最新代码\N{FULLWIDTH COLON} main 已快进更新至 origin/main。 工作区干净。 未运行测试。"
    )
    assert reply_brief("Done:\r\n\r\n1. Updated files.\r\n2. Tests passed.") == "Done: Updated files. Tests passed."
    assert reply_brief("Done.\n\n```text\nnot a preview\n```\n\nTests passed.") == "Done. Tests passed."


@pytest.mark.anyio
@pytest.mark.parametrize("notify_raises", [False, True])
@pytest.mark.parametrize(
    "scenario", ["completed", "save_failed", "question", "approval", "preparation_failed", "cancelled"]
)
async def test_root_notices_follow_host_settlement_and_replay_without_new_history(
    scenario: str, notify_raises: bool
) -> None:
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

    async def execute(admission, **kwargs: Any) -> RootRunOutcome:
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
                usage=RunUsageSummary(),
            ),
            environment=EnvironmentFinalization(state_publications=(), cleanup_errors=()),
            continuation=RootContinuationSelection(
                status="failed" if scenario == "save_failed" else "selected",
                error=OSError("disk unavailable") if scenario == "save_failed" else None,
            ),
            composition=cast(ObjectRef, SimpleNamespace(logical_digest="a" * 64)),
        )

    pushed: list[tuple[str, RootOperationNotice]] = []

    def notify(thread_id: str, notice: RootOperationNotice) -> None:
        pushed.append((thread_id, notice))
        if notify_raises:
            raise RuntimeError("Notification failure must not change settlement")

    from .test_root_run import capture

    settled = []
    delivered = Event()

    async def capture_project(**kwargs):
        admission = await capture(**kwargs)
        admission.published.value = SimpleNamespace(project_id="project-captured")
        return admission

    async def on_settled(project_id, operation):
        assert await coordinator.active(operation.receipt.thread_id) is None
        assert (await coordinator.get(operation.receipt.receipt_id)).status == operation.status
        settled.append((project_id, operation))
        delivered.set()
        if notify_raises:
            raise RuntimeError("Lifecycle delivery must not change settlement")

    coordinator = RootRunCoordinator(
        cast(Any, SimpleNamespace(capture=capture_project, execute=execute)),
        summary_hub=hub,
        notify=notify,
        on_settled=on_settled,
    )
    await coordinator.start()
    try:
        async with hub.subscribe() as subscription:
            cursor = subscription.cursor
            receipt = await coordinator.submit_prompt(thread_id="thread_test", prompt="Work")
            operation = await coordinator.wait(receipt.receipt_id)
            with fail_after(3):
                await delivered.wait()
            assert len(settled) == 1
            assert settled[0][0] == "project-captured"
            assert settled[0][1].status == operation.status
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
        assert pushed == [("thread_test", event.notice) for event in notices]
    finally:
        await coordinator.close(timeout_seconds=1)
        await hub.close()
