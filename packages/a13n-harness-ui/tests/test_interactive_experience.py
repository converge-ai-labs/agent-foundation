"""Observable terminal UX invariants without network access."""

from __future__ import annotations

import asyncio
import json
import os
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
from a13n_harness_ui.surfaces import SkillCatalogView, TaskPage
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
            {"message_id": source, "role": "user", "delta": "same words", "metadata": {"source_id": source}},
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
    assert status.usage.cost == Decimal("0.012346")
    assert status.usage.input_tokens == 200 and status.usage.cache_read_tokens == 160
    assert "cache write 20" in status.usage_details().lower()
    assert "$0.0123" in status.line(80)
    status.record_usage(record("three", None))
    assert status.usage.cost is None and "Cost: unknown" in status.usage_details()
    status.reset_usage()
    assert status.usage is None and status.requests == 0


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
    assert "[pending] task-1" in panel.lines()
    panel.restore(TaskPage(available=False))
    assert "unavailable" in panel.lines()[0] and not panel.tasks


def test_skill_completion_uses_authoritative_catalog_and_command_names_win() -> None:
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
    assert ("/inspect", "Skill · Authoritative skill") in registry.completions("/ins")
    assert registry.skill_references("/inspect this")[0].catalog_id == catalog.catalog_id
    assert registry.skill_references("/status") == ()
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
            pipe.send_text("\x1bOQ")
            await until(lambda: shell.renderer.tasks.expanded)
            pipe.send_text("\x03")
            await asyncio.wait_for(terminal, 3)
        finally:
            if shell.app.is_running:
                shell.app.exit()
            await terminal


def test_terminal_is_one_consumer_of_structured_media_input() -> None:
    from a13n_harness import HarnessEvent
    from a13n_harness.model_context import ModelInputEvent
    from a13n_stream_protocol import HarnessAguiObserver
    from pydantic_ai.messages import BinaryContent, ImageUrl

    content = [
        BinaryContent(data=b"private pixels", media_type="image/png", vendor_metadata={"image_object_id": "image-one"}),
        ImageUrl("https://example.test/picture.png"),
    ]
    event = HarnessEvent(
        thread_id="thread-one",
        run_id="run-one",
        sequence=1,
        occurred_at=datetime.now(UTC),
        event=ModelInputEvent(content=content),
    )
    renderer = StreamRenderer(Status())
    observed = HarnessAguiObserver().observe(event)
    for item in observed:
        renderer.ingest(item.type.value, item.model_dump(mode="json"))
    assert "image/png · 14 bytes" in _text(renderer)
    assert "https://example.test/picture.png" in _text(renderer)
    assert "private pixels" not in _text(renderer)
    assert observed[0].model_extra["metadata"]["image_object_id"] == "image-one"
