"""Observable terminal UX invariants without network access."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.usage import BoundedRequestUsage, ModelUsageRecord
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.commands import CommandRegistry
from a13n_harness_ui.interactive.diagnostics import exception_report
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
from a13n_harness_ui.interactive.shell import CliShell
from a13n_harness_ui.interactive.tasks import TaskPanel
from a13n_harness_ui.storage.usage import UsageTotals
from a13n_harness_ui.surfaces import RootOperationStatus, SkillCatalogView, TaskPage
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput


def _text(renderer: StreamRenderer) -> str:
    return "\n".join(block.source for block in renderer.transcript.blocks.values())


def test_local_input_is_immediate_and_deduplicates_only_explicit_source() -> None:
    renderer = StreamRenderer(Status())
    renderer.local_input("input-one", "same words")
    assert _text(renderer) == "> same words"
    for source in ("input-one", "input-two"):
        renderer.ingest(
            "TEXT_MESSAGE_CONTENT",
            {"messageId": source, "role": "user", "delta": "same words", "metadata": {"source_id": source}},
        )
    assert _text(renderer).count("same words") == 2


@pytest.mark.anyio
async def test_input_appears_before_admission_and_rejection_preserves_draft() -> None:
    release = asyncio.Event()

    async def execute(*args, **kwargs):
        await release.wait()
        raise ValueError("Admission rejected")

    backend = SimpleNamespace(
        execute=execute,
        interaction=AsyncMock(return_value=None),
        skill_catalog=AsyncMock(return_value=None),
        thread_id=None,
    )
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = backend
        shell.send_prompt("immediate input")
        assert "immediate input" in _text(shell.renderer)
        release.set()
        await shell.job
        assert shell.composer.text == "immediate input"
        assert "not admitted" in _text(shell.renderer)


def test_status_keeps_native_cache_counters_decimal_cost_and_unknown_cost() -> None:
    def record(identifier: str, cost: Decimal | None):
        return ModelUsageRecord(
            call_id="call_fixture",
            record_id=identifier,
            run_id="run-one",
            response_ordinal=0,
            agent_instance_id="root",
            response_state="complete",
            response_timestamp=datetime.now(UTC),
            request_usage=BoundedRequestUsage(
                input_tokens=100, output_tokens=20, cache_read_tokens=80, cache_write_tokens=10, cost=cost
            ),
            cost_source="unknown",
            pricing_status="not_reached",
        )

    status = Status(model="openai-codex:gpt-test")
    first = record("one", Decimal("0.012345"))
    status.record_usage(first)
    status.record_usage(first)
    status.record_usage(record("two", Decimal("0.000001")))
    assert status.requests == 2
    assert status.total_tokens == 240  # Cache read/write are subsets, not extra tokens.
    assert "240 total tokens" in status.usage_details()
    assert status.usage.cost == Decimal("0.012346")
    assert status.usage.input_tokens == 200 and status.usage.cache_read_tokens == 160
    assert "cache write 20" in status.usage_details().lower()
    assert "$0.0123" in status.line(80)
    status.record_usage(record("three", None))
    assert status.usage.cost == Decimal("0.012346")
    assert status.unknown_costs == 1
    assert "$0.0123+" in status.line()
    assert "1 unknown-cost responses" in status.usage_details()
    status.reset_usage()
    assert status.usage is None and status.requests == 0
    assert status.total_tokens is None


def _usage_totals(*, requests: int = 3, cost: Decimal = Decimal("0.3"), unknown: int = 0) -> UsageTotals:
    return UsageTotals(
        model_requests=requests,
        provider_receipts=1,
        tokens=(("input_tokens", 300), ("output_tokens", 60), ("cache_read_tokens", 180)),
        model_cost_usd=cost,
        unknown_model_costs=unknown,
        provider_costs=(("USD", Decimal("99")),),
        unknown_provider_costs=0,
        omitted_currency_receipts=0,
    )


@pytest.mark.parametrize("cost", [Decimal("0"), Decimal("0.123456"), None])
def test_status_restores_ledger_cost_coverage_without_provider_costs(cost: Decimal | None) -> None:
    status = Status()
    totals = _usage_totals(cost=Decimal("0.123456") if cost is None else cost, unknown=int(cost is None))
    status.restore_usage(totals)
    assert status.requests == 3
    assert status.usage.cost == totals.model_cost_usd
    assert status.cache_rate == 50
    assert status.total_tokens == 360
    assert "Observed conversation" in status.usage_details()
    # Reconciliation replaces rather than adds the same durable observations.
    status.restore_usage(totals)
    assert status.requests == 3
    assert status.usage.cost == totals.model_cost_usd
    # A ledger with only provider receipts is not proven zero model usage.
    status.restore_usage(_usage_totals(requests=0))
    assert status.usage is None and status.requests == 0
    assert status.total_tokens is None
    assert "unavailable" in status.usage_details()
    assert "cost --" in status.line()
    status.restore_usage(replace(_usage_totals(requests=1, cost=Decimal("0")), tokens=()))
    assert status.cache_rate is None
    assert status.total_tokens == 0
    assert "tokens 0" in status.line()
    assert "$0.0000" in status.line()


def test_task_panel_applies_distinct_tasks_at_same_version_and_ignores_stale() -> None:
    panel = TaskPanel()
    for identifier in ("task-1", "task-2"):
        panel.ingest(
            {
                "type": "task_changed",
                "operation_id": "task-change-one",
                "task_state_version": 2,
                "reason": "created",
                "task": {"id": identifier, "version": 1, "subject": identifier, "status": "pending"},
            }
        )
    assert len(panel.tasks) == 2
    panel.ingest(
        {
            "type": "task_changed",
            "operation_id": "task-change-two",
            "task_state_version": 1,
            "reason": "completed",
            "task": {"id": "task-1", "version": 2, "subject": "old", "status": "completed"},
        }
    )
    assert panel.tasks["task-1"].status == "pending"
    panel.expanded = True
    assert "[pending] 1 · task-1" in panel.lines()
    panel.restore(TaskPage(available=False))
    assert "unavailable" in panel.lines()[0] and not panel.tasks


def test_skill_completion_has_its_own_namespace_and_authoritative_references() -> None:
    registry = CommandRegistry()
    catalog = SkillCatalogView(
        catalog_id="a" * 64,
        context_kind="draft",
        items=tuple(
            {
                "item_id": str(index) * 64,
                "name": name,
                "description": "Authoritative skill",
                "source_id": "project",
                "logical_path": f"skills/{name}/SKILL.md",
            }
            for index, name in enumerate(("inspect", "status"), 1)
        ),
    )
    registry.set_skills(catalog)
    assert ("$inspect", "Authoritative skill") in registry.completions("$ins")
    assert registry.skill_references("use $inspect this")[0].catalog_id == catalog.catalog_id
    assert registry.skill_references("/status") == ()
    assert registry.skill_references("$status")[0].name == "status"
    assert registry.completions("/ins") == ()
    assert len(registry.completions("$")) == 2
    registry.set_skills(None)
    assert registry.skill_references("/inspect") == ()
    assert "/login" not in {value for value, _ in registry.completions("/")}


def test_exception_report_is_private_and_preserves_actionable_resume_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    request = CliRequest(config_path=tmp_path / "custom config.yaml", data_root=tmp_path / "data root")
    local_secret = "this-local-value-must-not-be-included"
    try:
        raise RuntimeError("fixture failure")
    except RuntimeError as error:
        message = exception_report(
            error, session_id="thread-one", phase="terminal", request=request, directory=tmp_path
        )
    report = next(tmp_path.glob("a13n-harness-ui-error-*.json"))
    content = report.read_text()
    assert local_secret not in content and "locals" not in content
    assert json.loads(content)["phase"] == "terminal"
    if os.name != "nt":
        assert report.stat().st_mode & 0o777 == 0o600
    assert "--config" in message and "--data-root" in message and "--resume thread-one" in message
    assert "Nothing was uploaded" in message


@pytest.mark.anyio
async def test_ctrl_c_feedback_edit_disarms_exit_and_f2_toggles() -> None:
    async def until(predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(0.01)

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        terminal = asyncio.create_task(shell.app.run_async())
        try:
            await until(lambda: shell.app.is_running)
            pipe.send_text("draft\x03")
            await until(lambda: "again within 2 seconds" in _text(shell.renderer))
            assert shell.app.is_running and shell.composer.text == ""
            pipe.send_text("edited")
            await until(lambda: shell.composer.text == "edited")
            pipe.send_text("\x03")
            await until(lambda: shell.composer.text == "")
            assert shell.app.is_running
            expanded = shell.renderer.tasks.expanded
            pipe.send_text("\x1bOQ")
            await until(lambda: shell.renderer.tasks.expanded != expanded)
            pipe.send_text("\x03")
            await asyncio.wait_for(terminal, 3)
        finally:
            if shell.app.is_running:
                shell.app.exit()
            await terminal


def test_terminal_is_one_consumer_of_structured_media_input() -> None:
    from a13n_harness import ContentItem, ContentMetadata, HarnessEvent
    from a13n_harness.events import input_events
    from a13n_stream_protocol import HarnessAguiObserver
    from pydantic_ai.messages import BinaryContent, ImageUrl

    content = [
        ContentItem(
            BinaryContent(data=b"private pixels", media_type="image/png"),
            ContentMetadata(image_object_id="image-one"),
        ),
        ImageUrl("https://example.test/picture.png"),
    ]
    renderer = StreamRenderer(Status())
    observer = HarnessAguiObserver()
    observed = tuple(
        item
        for sequence, event in enumerate(input_events(content, source="user", input_id="input-one"))
        for item in observer.observe(
            HarnessEvent(
                thread_id="thread-one", run_id="run-one", sequence=sequence, occurred_at=datetime.now(UTC), event=event
            )
        )
    )
    for item in observed:
        renderer.ingest(item.type.value, item.model_dump(mode="json"))
    assert "image/png · 14 bytes" in _text(renderer)
    assert "https://example.test/picture.png" in _text(renderer)
    assert "private pixels" not in _text(renderer)
    assert observed[0].metadata["image_object_id"] == "image-one"


@pytest.mark.anyio
@pytest.mark.parametrize("fragmented", [False, True])
@pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled", "error"])
async def test_live_cost_and_zero_context_are_projected_before_completion(
    fragmented: bool, terminal_status: str, tmp_path: Path
) -> None:
    from a13n_harness_ui.interactive.backend import SessionBackend
    from a13n_harness_ui.live import HarnessUiLiveHub
    from a13n_stream_protocol import fragment_custom_event
    from ag_ui.core import CustomEvent

    root = ModelUsageRecord(
        call_id="call_fixture",
        record_id="root-1",
        run_id="run-one",
        response_ordinal=0,
        agent_instance_id="root",
        response_state="complete",
        response_timestamp=datetime.now(UTC),
        request_usage=BoundedRequestUsage(input_tokens=100, output_tokens=20, cost=Decimal("0.125")),
        cost_source="unknown",
        pricing_status="disabled",
    )
    zero = root.model_copy(
        update={"record_id": "root-2", "response_ordinal": 1, "request_usage": BoundedRequestUsage(cost=Decimal("0"))}
    )
    child = root.model_copy(update={"record_id": "child-1", "parent_agent_instance_id": "root"})
    report = CustomEvent(
        name="a13n.usage",
        value={
            "event": {
                "schema_version": "1",
                "payload": {
                    "type": "usage_report",
                    "records": [item.model_dump(mode="json") for item in (root, root, child, zero)],
                    "padding": "x" * (50000 if fragmented else 0),
                },
            }
        },
    )
    events = fragment_custom_event(report, identity="usage-one")
    assert (len(events) > 1) == fragmented
    hub = HarnessUiLiveHub()
    release, updated = asyncio.Event(), asyncio.Event()

    async def submit(**kwargs):
        assert status.requests == 3  # The prior Runs stay visible at admission.
        assert status.usage.cost == Decimal("0.3")
        await hub.publish(
            run_kind="root",
            root_thread_id="thread-one",
            parent_thread_id=None,
            thread_id="thread-one",
            run_id="run-one",
            events=events,
        )
        return SimpleNamespace(receipt_id="receipt-one")

    async def wait(receipt_id):
        await release.wait()
        if terminal_status == "error":
            raise RuntimeError("wait failed")
        return SimpleNamespace(status=RootOperationStatus(terminal_status), outcome=None, failure=None, goal=None)

    live_totals = replace(
        _usage_totals(requests=6, cost=Decimal("0.55")),
        tokens=(("input_tokens", 500), ("output_tokens", 100), ("cache_read_tokens", 180)),
    )
    final_totals = replace(
        _usage_totals(requests=7, cost=Decimal("0.625")),
        tokens=(("input_tokens", 500), ("output_tokens", 100), ("cache_read_tokens", 180)),
    )
    app = SimpleNamespace(
        live_events=hub.subscribe,
        submit_thread=submit,
        wait_root_operation=wait,
        get_root_operation=AsyncMock(return_value=SimpleNamespace(goal=None)),
        context_usage=AsyncMock(return_value=SimpleNamespace(latest_request_tokens=0)),
        thread_usage=AsyncMock(
            side_effect=[
                SimpleNamespace(combined=_usage_totals()),
                SimpleNamespace(combined=live_totals),
                SimpleNamespace(combined=final_totals),
            ]
        ),
    )
    status = Status(state="working", context_window=350000)
    backend = SessionBackend(app, CliRequest(), tmp_path, status)
    backend.refresh = AsyncMock(return_value=True)
    backend.ensure_session = AsyncMock(return_value="thread-one")

    async def flush():
        if status.requests:
            updated.set()

    renderer = StreamRenderer(status)
    task = asyncio.create_task(backend.execute(renderer, prompt="test", flush=flush))
    try:
        await asyncio.wait_for(updated.wait(), 2)
        assert not task.done()
        assert not renderer.gap
        assert status.requests == 6  # Three historical + two root + one child; duplicate excluded.
        assert status.usage.cost == Decimal("0.55")
        assert status.cache_rate == 30  # Weighted counters, not average percentages.
        assert status.context_tokens == 0
        assert "Working" in status.line(60) and "$0.5500" in status.line(60)
        assert "ctx 0 (0%)" in status.line(60)
        assert "cache" in status.line(120) and "out " not in status.line(120)
    finally:
        release.set()
        try:
            if terminal_status == "error":
                with pytest.raises(RuntimeError, match="wait failed"):
                    await asyncio.wait_for(task, 2)
            else:
                await asyncio.wait_for(task, 2)
        finally:
            await hub.close()
    # The terminal ledger also contains a response missed by the live stream.
    assert status.requests == 7
    assert status.usage.cost == Decimal("0.625")
    assert status.cache_rate == 30
    assert status.context_tokens == 0
    assert not status._usage_ids
    assert app.thread_usage.call_count == 3  # One cached projection refresh per complete live report.


@pytest.mark.anyio
@pytest.mark.parametrize("gap", [False, True])
@pytest.mark.parametrize("output_omitted", [False, True])
async def test_recovered_final_answer_keeps_markdown_separate_from_notices(
    tmp_path: Path, gap: bool, output_omitted: bool
) -> None:
    from a13n_harness_ui.interactive.backend import SessionBackend
    from a13n_harness_ui.live import HarnessUiLiveHub

    answer = "# Recovered heading\n\n**Important result**\n\n```python\nprint('done')\n```"
    hub = HarnessUiLiveHub()
    renderer = StreamRenderer(Status())
    if gap:
        renderer.ingest("TEXT_MESSAGE_CONTENT", {"messageId": "partial", "delta": "Incomplete **answer"})
    renderer.gap = gap
    outcome = SimpleNamespace(
        execution=SimpleNamespace(output=answer, output_omitted=output_omitted),
        environment=SimpleNamespace(cleanup_failures=()),
    )
    app = SimpleNamespace(
        live_events=hub.subscribe,
        submit_thread=AsyncMock(return_value=SimpleNamespace(receipt_id="receipt-one")),
        get_root_operation=AsyncMock(return_value=SimpleNamespace(goal=None)),
        wait_root_operation=AsyncMock(
            return_value=SimpleNamespace(status=RootOperationStatus.completed, outcome=outcome, failure=None, goal=None)
        ),
        context_usage=AsyncMock(return_value=SimpleNamespace(latest_request_tokens=0)),
        thread_usage=AsyncMock(return_value=SimpleNamespace(combined=_usage_totals())),
    )
    backend = SessionBackend(app, CliRequest(), tmp_path, renderer.status)
    backend.refresh = AsyncMock(return_value=True)
    backend.ensure_session = AsyncMock(return_value="thread-one")
    try:
        assert await backend.execute(renderer, prompt="test") == ""
        blocks = list(renderer.transcript.blocks.values())
        notices = [block for block in blocks if block.kind == "notice"]
        assert len(notices) == int(gap) + int(output_omitted)
        assert all(not block.markdown for block in notices)
        if output_omitted:
            assert answer not in _text(renderer)
            assert "/history" in notices[-1].source
        else:
            recovered = blocks[-1]
            assert recovered.source == answer + "\n"
            assert recovered.markdown and not recovered.streaming
            renderer.transcript.render(80)
            fragments = [fragment for row in recovered.rows for fragment in row]
            assert any("bold" in style and "Important result" in text for style, text in fragments)
            assert "# Recovered heading" not in "".join(text for _, text in fragments)
        # A later normal streamed response still takes the Markdown path.
        renderer.ingest("TEXT_MESSAGE_CONTENT", {"messageId": "next", "delta": "**Next answer**"})
        assert list(renderer.transcript.blocks.values())[-1].markdown
    finally:
        renderer.transcript.close()
        await hub.close()


@pytest.mark.anyio
async def test_subscription_close_invalidates_outliving_child_process_observations(tmp_path: Path) -> None:
    from a13n_harness_ui.interactive.backend import SessionBackend
    from a13n_harness_ui.live import HarnessUiLiveHub
    from a13n_harness_ui.surfaces import RootOperationStatus

    hub = HarnessUiLiveHub()
    renderer = StreamRenderer(Status())

    async def submit(**kwargs):
        renderer._observe_shell_result(
            json.dumps({"process_id": "process-child", "status": {"phase": "running"}}), "sleep 30", "child-run"
        )
        assert "Background 1" in renderer.background_hint
        return SimpleNamespace(receipt_id="receipt-one")

    app = SimpleNamespace(
        live_events=hub.subscribe,
        submit_thread=submit,
        get_root_operation=AsyncMock(return_value=SimpleNamespace(goal=None)),
        wait_root_operation=AsyncMock(
            return_value=SimpleNamespace(status=RootOperationStatus.cancelled, outcome=None, failure=None, goal=None)
        ),
        context_usage=AsyncMock(return_value=SimpleNamespace(latest_request_tokens=0)),
        thread_usage=AsyncMock(return_value=SimpleNamespace(combined=_usage_totals(requests=0))),
    )
    backend = SessionBackend(app, CliRequest(), tmp_path, renderer.status)
    backend.refresh = AsyncMock(return_value=True)
    backend.ensure_session = AsyncMock(return_value="thread-one")
    try:
        await backend.execute(renderer, prompt="test")
        assert renderer.background_hint == ""
        assert "process-child · unavailable" in renderer.process_details()
    finally:
        renderer.transcript.close()
        await hub.close()


@pytest.mark.anyio
@pytest.mark.parametrize("execution_id", [None, "execution-one"])
async def test_subagent_inspection_discards_results_after_conversation_switch(execution_id) -> None:
    from a13n_harness_ui.interactive.backend import SessionBackend
    from a13n_harness_ui.surfaces import ChildExecutionPage, ReviewView

    pending = asyncio.Future()

    async def delayed(**kwargs):
        assert kwargs["parent_thread_id"] == "thread-one"
        return await pending

    backend = SessionBackend(
        SimpleNamespace(query_child_executions=delayed, child_review=delayed), CliRequest(), Path.cwd(), Status()
    )
    backend.thread_id = "thread-one"
    query = asyncio.create_task(backend.subagents(execution_id))
    await asyncio.sleep(0)
    backend.thread_id = "thread-two"
    pending.set_result(
        ChildExecutionPage(executions=(), total=21, next_cursor="old-cursor")
        if execution_id is None
        else ReviewView(lifecycle="closed", kind="child", title="Old child", content="Old output")
    )
    assert "discarded" in await query
    assert backend._child_page_thread is None
    assert "No next page" in await backend.subagents("next")


@pytest.mark.anyio
async def test_task_panel_has_a_full_width_separator_in_the_terminal() -> None:
    from a13n_harness_ui.surfaces import TaskView

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.renderer.append("tool output", kind="tool")
        shell.renderer.tasks.restore(
            TaskPage(tasks=(TaskView(task_id="task-1", version=1, subject="Do work", status="pending"),))
        )
        terminal = asyncio.create_task(shell.app.run_async())
        try:
            for _ in range(100):
                screen = shell.app.renderer.last_rendered_screen
                if screen is not None:
                    break
                await asyncio.sleep(0.01)
            assert screen is not None
            lines = ["".join(row[x].char for x in range(80)) for _, row in sorted(screen.data_buffer.items())]
            index = next(index for index, line in enumerate(lines) if "Tasks ·" in line)
            assert lines[index - 1] == "─" * 80
        finally:
            shell.app.exit()
            await terminal
            shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_activity_refresh_is_thread_scoped_and_does_not_publish_stale_results() -> None:
    from a13n_harness_ui.surfaces import ChildExecutionPage

    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        query = AsyncMock(return_value=ChildExecutionPage(executions=(), total=3))
        shell.backend = SimpleNamespace(thread_id="thread-one", app=SimpleNamespace(query_child_executions=query))
        await shell._refresh_activities()
        assert "Subagents 3 total · /subagents" in shell._activity_hint()
        query.assert_awaited_once_with(parent_thread_id="thread-one", limit=1)
        pending = asyncio.Future()

        async def delayed(**kwargs):
            return await pending

        query.side_effect = delayed
        refresh = asyncio.create_task(shell._refresh_activities())
        await asyncio.sleep(0)
        shell.backend.thread_id = "thread-two"
        pending.set_result(ChildExecutionPage(executions=(), total=7))
        await refresh
        assert shell._activity_hint() == ""
        query.side_effect = RuntimeError("offline")
        await shell._refresh_activities()
        assert "Subagents unavailable" in shell._activity_hint()
        shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_subagent_inspection_uses_app_pages_and_review_without_execution() -> None:
    from a13n_harness_ui.interactive.backend import SessionBackend
    from a13n_harness_ui.surfaces import ChildExecutionPage, ReviewView

    query = AsyncMock(return_value=ChildExecutionPage(executions=(), total=21, next_cursor="page-two"))
    review = AsyncMock(return_value=ReviewView(lifecycle="closed", kind="child", title="Child", content="Saved output"))
    app = SimpleNamespace(query_child_executions=query, child_review=review)
    backend = SessionBackend(app, CliRequest(), Path.cwd(), Status())
    assert "No subagent" in await backend.subagents()
    backend.thread_id = "thread-one"
    listing = await backend.subagents()
    assert "0 shown · 21 executions" in listing
    assert "/subagents next" in listing
    await backend.subagents("next")
    query.assert_awaited_with(parent_thread_id="thread-one", cursor="page-two", limit=20)
    assert "Saved output" in await backend.subagents("execution-one")
    review.assert_awaited_once_with(parent_thread_id="thread-one", execution_id="execution-one")
    backend.thread_id = "thread-two"
    assert "No next page" in await backend.subagents("next")


def test_model_and_agent_have_independent_commands_and_help() -> None:
    registry = CommandRegistry()
    assert registry.parse("/model").command is not registry.parse("/agent").command
    assert registry.completions("/model")[0][0] == "/model"
    assert "[agent-id]" in registry.help("agent")
    assert "[model-id|default|defaults]" in registry.help("model")
    assert registry.parse("/model defaults").arguments == ("defaults",)
    for command in ("/model other", "/agent other"):
        with pytest.raises(ValueError, match="unavailable"):
            registry.parse(command, busy=True)
    assert registry.parse("/ps", busy=True).command.name == "ps"
    assert registry.parse("/subagents", busy=True).command.name == "subagents"


@pytest.mark.parametrize(
    "tokens, label",
    [(0, "0"), (999, "999"), (1000, "1.0K"), (12560, "12.6K"), (1000000, "1.0M"), (1256000, "1.3M")],
)
def test_status_token_abbreviations_keep_one_decimal_at_every_width(tokens: int, label: str) -> None:
    status = Status(state="ready", usage=BoundedRequestUsage(input_tokens=tokens))
    for width in (24, 80, 160, None):
        assert f"{'tok' if width == 24 else 'tokens'} {label}" in status.line(width)
    assert f"{tokens:,} total tokens" in status.usage_details()


@pytest.mark.parametrize("baseline", [False, True])
@pytest.mark.parametrize("source", ["user", "steering"])
def test_composed_input_display_renders_once_from_live_events_or_snapshot(baseline: bool, source: str) -> None:
    from a13n_stream_protocol.display import DisplayFold

    fold = DisplayFold("run")
    renderer = StreamRenderer(Status())
    for index, text in enumerate(("Keep ", "this direction")):
        event = {
            "type": "CUSTOM",
            "name": f"a13n.input.{source}",
            "metadata": {"source_id": "input-one", "harness_ui": {"composer": {"index": index}}},
            "value": {
                "event": {
                    "message_id": f"message-{index}",
                    "input_id": "input-one",
                    "source": source,
                    "role": "user",
                    "content": text,
                }
            },
        }
        batch = fold.fold([event])[0]
        if not baseline:
            for _ in range(2):
                renderer.ingest(event["type"], event, run_id="run", display_position=fold.position, item=batch.item)
    if baseline:
        for _ in range(2):
            renderer.ingest(
                "CUSTOM",
                {"name": "a13n.display.snapshot", "value": fold.export().model_dump(mode="json")},
                run_id="run",
            )
    assert _text(renderer) == "> Keep this direction"
    assert all(block.kind == "user" for block in renderer.transcript.blocks.values())
    assert not renderer.gap


@pytest.mark.parametrize("baseline", [False, True])
def test_compact_tool_arguments_complete_before_result(baseline: bool) -> None:
    from a13n_stream_protocol.display import DisplayFold

    fold = DisplayFold("run")
    renderer = StreamRenderer(Status())
    events = [
        {"type": "TOOL_CALL_START", "toolCallId": "call", "toolCallName": "shell_exec"},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "call", "delta": '{"command":"echo hello"}'},
        {"type": "TOOL_CALL_END", "toolCallId": "call"},
    ]
    for event in events:
        batch = fold.fold([event])[0]
        if not baseline:
            renderer.ingest(event["type"], event, run_id="run", display_position=fold.position, item=batch.item)
    if baseline:
        renderer.ingest(
            "CUSTOM",
            {"name": "a13n.display.snapshot", "value": fold.export().model_dump(mode="json")},
            run_id="run",
        )
    preview = renderer._tools[("run", "call")]
    assert "echo hello" in preview.arguments
    assert preview.summary == "echo hello"
    assert preview.parts is None


@pytest.mark.parametrize("cut", range(7))
def test_active_snapshot_raw_suffix_keeps_cli_text_and_tools_single(cut: int) -> None:
    from a13n_stream_protocol.display import DisplayFold

    events = [
        {"type": "TEXT_MESSAGE_START", "messageId": "message", "role": "assistant"},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "message", "delta": "hello"},
        {"type": "TOOL_CALL_START", "toolCallId": "call", "toolCallName": "shell_exec"},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "call", "delta": '{"command":"echo'},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "message", "delta": " world"},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "call", "delta": ' hello"}'},
        {"type": "TOOL_CALL_END", "toolCallId": "call"},
    ]
    fold = DisplayFold("run", full_content=True)
    fold.fold(events[:cut])
    renderer = StreamRenderer(Status())
    renderer.ingest(
        "CUSTOM", {"name": "a13n.display.snapshot", "value": fold.export().model_dump(mode="json")}, run_id="run"
    )
    for payload in events[cut:]:
        observation = fold.fold([payload])[0]
        renderer.ingest(payload["type"], payload, run_id="run", display_position=fold.position, item=observation.item)
    assert len(renderer._display_blocks) == 1
    assert len(renderer._tools) == 1
    import json

    assert json.loads(renderer._tools[("run", "call")].arguments) == {"command": "echo hello"}
    assert renderer._tools[("run", "call")].summary == "echo hello"
    assert (
        next(item for item in renderer._display_items.values() if item.kind == "text_message").content["text"]
        == "hello world"
    )
    assert not renderer.gap
