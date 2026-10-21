"""The terminal repairs compact display loss without resubmitting agent work."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.display_history import DisplayPublication
from a13n_harness_ui.interactive.backend import SessionBackend
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
from a13n_harness_ui.live import HarnessUiLiveHub
from a13n_harness_ui.surfaces import RootOperationStatus
from a13n_stream_protocol.display import DisplayFold
from ag_ui.core import CustomEvent, ToolCallResultEvent, ToolCallStartEvent

from .test_interactive_experience import _text, _usage_totals

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("loss", ["reset", "sequence", "terminal"])
async def test_root_display_recovers_tool_results_and_keeps_streaming_before_completion(tmp_path, loss):
    hub = HarnessUiLiveHub()
    fold = DisplayFold("run-one", full_content=True)
    renderer = StreamRenderer(Status(mode="detailed"))
    updated, finish = asyncio.Event(), asyncio.Event()
    resume, paused = asyncio.Event(), asyncio.Event()
    resume.set()

    async def publish(events, *, reset=False, drop=False):
        observations = fold.fold(events)
        cut = DisplayPublication(tuple(observations), tuple(fold.items.values()), fold.export_continuation(), reset)
        if not drop:
            await hub.publish(
                run_kind="root",
                root_thread_id="thread-one",
                parent_thread_id=None,
                thread_id="thread-one",
                run_id="run-one",
                events=(),
                display=cut,
            )

    async def submit(**kwargs):
        await publish(
            [
                {"type": "TOOL_CALL_START", "toolCallId": "edit-one", "toolCallName": "edit"},
                {"type": "TOOL_CALL_ARGS", "toolCallId": "edit-one", "delta": '{"file_path":"example.py"}'},
                {"type": "TOOL_CALL_END", "toolCallId": "edit-one"},
            ]
        )
        return SimpleNamespace(receipt_id="receipt-one")

    async def wait(receipt_id):
        await finish.wait()
        return SimpleNamespace(
            status=RootOperationStatus.completed,
            goal=None,
            outcome=SimpleNamespace(
                execution=SimpleNamespace(output="Recovered answer, still live.", output_omitted=False),
                environment=SimpleNamespace(cleanup_failures=()),
            ),
        )

    app = SimpleNamespace(
        live_events=hub.subscribe,
        submit_thread=AsyncMock(side_effect=submit),
        get_root_operation=AsyncMock(return_value=SimpleNamespace(goal=None)),
        wait_root_operation=wait,
        context_usage=AsyncMock(return_value=SimpleNamespace(latest_request_tokens=0)),
        thread_usage=AsyncMock(return_value=SimpleNamespace(combined=_usage_totals())),
    )
    backend = SessionBackend(app, CliRequest(), tmp_path, renderer.status)
    backend.refresh = AsyncMock(return_value=True)
    backend.ensure_session = AsyncMock(return_value="thread-one")

    async def flush():
        updated.set()
        if not resume.is_set():
            paused.set()
            await resume.wait()

    async def until(predicate):
        async with asyncio.timeout(2):
            while not predicate():
                updated.clear()
                await updated.wait()

    task = asyncio.create_task(backend.execute(renderer, prompt="Edit once", flush=flush))
    try:
        await until(lambda: "running" in _text(renderer))
        # Hold rendering while root reset and a child result enter the same
        # subscription. Recovery must preserve that child's queued events.
        resume.clear()
        await hub.publish(
            run_kind="child",
            root_thread_id="thread-one",
            parent_thread_id="thread-one",
            thread_id="child-one",
            run_id="child-run",
            execution_id="child-exec",
            events=[ToolCallStartEvent(tool_call_id="child-tool", tool_call_name="child_edit")],
        )
        async with asyncio.timeout(2):
            await paused.wait()
        await publish(
            [
                {"type": "TOOL_CALL_RESULT", "toolCallId": "edit-one", "messageId": "tool-result", "content": "done"},
                {"type": "TEXT_MESSAGE_START", "messageId": "answer", "role": "assistant"},
                {"type": "TEXT_MESSAGE_CONTENT", "messageId": "answer", "delta": "Recovered answer"},
            ],
            reset=loss != "sequence",
            drop=loss == "sequence",
        )
        if loss == "sequence":
            await publish([{"type": "TEXT_MESSAGE_CONTENT", "messageId": "answer", "delta": ""}])
        await hub.publish(
            run_kind="child",
            root_thread_id="thread-one",
            parent_thread_id="thread-one",
            thread_id="child-one",
            run_id="child-run",
            execution_id="child-exec",
            events=[ToolCallResultEvent(tool_call_id="child-tool", message_id="child-result", content="done")],
        )
        if loss == "terminal":
            await publish(
                [
                    {"type": "TEXT_MESSAGE_CONTENT", "messageId": "answer", "delta": ", still live."},
                    {"type": "TEXT_MESSAGE_END", "messageId": "answer"},
                ]
            )
            await hub.finish_root(thread_id="thread-one", run_id="run-one", saved_continuation_id="saved-one")
            assert "thread-one" not in hub._root_streams
            finish.set()
        resume.set()
        await until(lambda: "Recovered answer" in _text(renderer) and "running" not in _text(renderer))
        assert not renderer.gap
        assert "child_edit" in _text(renderer)
        if loss == "terminal":
            await task
            assert _text(renderer).count("Recovered answer, still live.") == 1
            assert "Recovered final answer" not in _text(renderer)
            app.submit_thread.assert_awaited_once()
            assert not hub._subscribers
            return
        assert not task.done()
        assert len(hub._subscribers) == 1
        # A second reset of the same state must not duplicate rendered output.
        await publish([], reset=True)
        await publish(
            [
                {"type": "TEXT_MESSAGE_CONTENT", "messageId": "answer", "delta": ", still live."},
                {"type": "TEXT_MESSAGE_END", "messageId": "answer"},
            ]
        )
        await until(lambda: "Recovered answer, still live." in _text(renderer))
        finish.set()
        assert await task == ""
        assert _text(renderer).count("Recovered answer, still live.") == 1
        assert "Recovered final answer" not in _text(renderer)
        app.submit_thread.assert_awaited_once()
        assert not hub._subscribers
    finally:
        resume.set()
        finish.set()
        await task
        renderer.transcript.close()
        await hub.close()


async def test_completion_alerts_skip_consumed_results_and_coalesce_a_burst():
    from a13n_harness_ui.interactive.shell import CliShell
    from a13n_harness_ui.live import HarnessUiSummaryHub
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import DummyInput
    from prompt_toolkit.output import DummyOutput

    hub = HarnessUiSummaryHub(epoch="test")
    initialized, inspected, release = (asyncio.Event() for _ in range(3))
    records = []

    async def executions(*args, **kwargs):
        initialized.set()
        return list(records)

    async def active(*args):
        inspected.set()
        return None

    app = SimpleNamespace(summary_events=hub.subscribe, tool_executions=executions, active_root_operation=active)
    with create_app_session(input=DummyInput(), output=DummyOutput()):
        shell = CliShell(CliRequest())
    shell.backend = SimpleNamespace(app=app, thread_id="thread-one", observed_receipts={})

    async def foreground():
        await release.wait()
        return ""

    watcher = asyncio.create_task(shell._watch_background_runs())
    try:
        async with asyncio.timeout(3):
            await initialized.wait()
            shell.job = asyncio.create_task(foreground())
            for index in range(14):
                records.append(
                    SimpleNamespace(
                        execution_id=f"exec-{index}",
                        tool_name="shell_exec",
                        completed_at="now",
                        foreground_released=True,
                        delivery_acknowledged=index < 2,
                        status="failed" if index == 2 else "succeeded",
                    )
                )
            await hub.publish(kind="thread", thread_id="thread-one")
            assert "Tool execution" not in _text(shell.renderer)
            release.set()
            await inspected.wait()
            notices = [block.source for block in shell.renderer.transcript.blocks.values() if block.kind == "notice"]
            assert notices == ["Tool executions · 1 failed, 11 succeeded. Use /executions for details.\n"]
            inspected.clear()
            await hub.publish(kind="thread", thread_id="thread-one")
            await inspected.wait()
            assert _text(shell.renderer).count("Tool executions") == 1
            assert all(not record.delivery_acknowledged for record in records[2:])
    finally:
        release.set()
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        if shell.job is not None:
            await shell.job
        shell.renderer.transcript.close()
        await hub.close()


async def test_baseline_refresh_is_run_scoped_and_released_with_subscription():
    hub = HarnessUiLiveHub()
    fold = DisplayFold("run-one", full_content=True)
    observations = fold.fold([{"type": "TEXT_MESSAGE_CONTENT", "messageId": "answer", "delta": "saved"}])
    async with hub.subscribe(root_thread_id="thread-one") as subscription:
        assert subscription.capture_root_stream("run-one") is None
        await hub.publish(
            run_kind="root",
            root_thread_id="thread-one",
            parent_thread_id=None,
            thread_id="thread-one",
            run_id="run-one",
            events=(),
            display=DisplayPublication(
                tuple(observations), tuple(fold.items.values()), fold.export_continuation(), True
            ),
        )
        assert subscription.capture_root_stream("other-run") is None
        await hub.finish_root(thread_id="thread-one", run_id="run-one", saved_continuation_id="saved-one")
        assert subscription.capture_root_stream("run-one") is not None
        async with hub.subscribe(root_thread_id="thread-one") as late:
            assert late.root_stream is None
            assert late.capture_root_stream("run-one") is None
    assert subscription.capture_root_stream("run-one") is None
    await hub.close()


async def test_root_snapshot_preserves_unrelated_gaps_and_partial_child_frames():
    from a13n_stream_protocol import fragment_custom_event

    renderer = StreamRenderer(Status(mode="detailed"))
    fold = DisplayFold("run-one", full_content=True)
    child = CustomEvent(
        name="a13n.pydantic_ai.function_tool_result",
        value={
            "event": {
                "part": {
                    "part_kind": "tool-return",
                    "tool_call_id": "child-tool",
                    "tool_name": "edit",
                    "content": "x" * 50000,
                }
            }
        },
    )
    frames = fragment_custom_event(child, identity="child-fragment")
    assert len(frames) > 1
    try:
        renderer.ingest(
            "TOOL_CALL_START", {"toolCallId": "child-tool", "toolCallName": "edit"}, child=True, run_id="child"
        )
        assert "running" in _text(renderer)
        renderer.ingest("CUSTOM", frames[0].model_dump(mode="json", by_alias=True), child=True, run_id="child")
        renderer.ingest("CUSTOM", {"name": "a13n.display.reset", "value": {}}, run_id="run-one")
        renderer.ingest("CUSTOM", None, child=True, run_id="other-child")
        renderer.begin_display_snapshot()
        renderer.ingest(
            "CUSTOM",
            {
                "name": "a13n.display.snapshot",
                "value": {"items": [], "continuation": fold.export_continuation().model_dump(mode="json")},
            },
            run_id="run-one",
        )
        assert not renderer.display_gap
        assert renderer.gap
        for frame in frames[1:]:
            renderer.ingest("CUSTOM", frame.model_dump(mode="json", by_alias=True), child=True, run_id="child")
        assert not renderer._custom_events.gap
        assert "running" not in _text(renderer)
    finally:
        renderer.transcript.close()
