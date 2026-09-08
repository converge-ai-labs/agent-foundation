from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.commands import CommandRegistry
from a13n_harness_ui.interactive.decisions import DecisionInteraction
from a13n_harness_ui.interactive.history import HistoryBrowser
from a13n_harness_ui.interactive.rendering import Status
from a13n_harness_ui.interactive.shell import CliShell, SlashCompleter
from a13n_harness_ui.surfaces import (
    ApprovalRequestView,
    DecisionBatchView,
    SkillCatalogItemView,
    SkillCatalogView,
    TranscriptPage,
)
from prompt_toolkit.application import create_app_session
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput


def test_dollar_completer_filters_describes_and_replaces_only_the_current_token() -> None:
    registry = CommandRegistry()
    registry.set_skills(
        SkillCatalogView(
            catalog_id="a" * 64,
            context_kind="idle",
            items=tuple(
                SkillCatalogItemView(
                    item_id=digit * 64,
                    name=name,
                    description="Brief\n description " * 30,
                    source_id="local",
                    logical_path=f"{name}/SKILL.md",
                )
                for digit, name in (("1", "review"), ("2", "research"), ("3", "status"))
            ),
        )
    )
    completer = SlashCompleter(registry)
    matches = list(completer.get_completions(Document("Please use $rev"), CompleteEvent()))
    assert len(matches) == 1
    assert matches[0].text == "$review"
    assert matches[0].start_position == -4
    assert "Brief description" in matches[0].display_meta_text
    assert len(matches[0].display_meta_text) <= 160
    assert list(completer.get_completions(Document("Please use $unknown"), CompleteEvent())) == []
    assert [item.text for item in completer.get_completions(Document("$"), CompleteEvent())] == [
        "$research",
        "$review",
        "$status",
    ]
    assert registry.parse("/status").command.name == "status"
    with pytest.raises(ValueError, match="Unknown command"):
        registry.parse("/research")
    assert [reference.name for reference in registry.skill_references("Use $review and $status then $review")] == [
        "review",
        "status",
    ]


@pytest.mark.anyio
async def test_history_has_one_loader_and_can_retry_after_a_conflict() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def load(cursor, continuation):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
            raise ValueError("The selected continuation changed")
        return TranscriptPage(entries=(), total=0)

    browser = HistoryBrowser(Status(), load, lambda: None)
    task = asyncio.create_task(browser.navigate())
    try:
        await entered.wait()
        await browser.navigate()
        assert calls == 1
        release.set()
        await task
        assert "End reloads latest" in browser.message
        assert browser.page is None and not browser.loading
        await browser.navigate()
        assert browser.page is not None and calls == 2
    finally:
        release.set()
        await task
        browser.close()


@pytest.mark.anyio
async def test_history_ctrl_c_does_not_answer_or_cancel_a_pending_approval() -> None:
    async def until(predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(0.01)

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = SimpleNamespace(
            thread_id="thread-pending",
            app=SimpleNamespace(get_thread_transcript=AsyncMock(return_value=TranscriptPage(entries=(), total=0))),
            cancel=AsyncMock(),
        )
        shell.composer.text = "preserve draft"
        shell._save_draft()
        decision = DecisionInteraction(
            DecisionBatchView(
                continuation_id="a" * 64,
                requests=(
                    ApprovalRequestView(
                        request_id="shell-one", tool_name="shell_exec", arguments={"command": "example"}
                    ),
                ),
            )
        )
        shell.interaction = decision
        shell.selection = decision.selection()
        selected = shell.selection
        saved = shell._saved_draft
        task = asyncio.create_task(shell.app.run_async())
        try:
            await until(lambda: shell.app.is_running)
            pipe.send_text("\x14")
            await until(lambda: shell.history_browser is not None and shell.history_browser.page is not None)
            pipe.send_text("\x03")
            await until(lambda: shell.history_browser is None)
            assert shell.interaction is decision and decision.responses == []
            assert shell.selection is selected and shell._saved_draft is saved
            assert saved is not None and saved.text == "preserve draft"
            shell.backend.cancel.assert_not_awaited()
        finally:
            shell.close_history()
            shell.app.exit()
            await task
            shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_usage_without_a_selected_thread_does_not_create_one_or_open_reset() -> None:
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest(), status=Status(model="openai:test"))
        shell.backend = SimpleNamespace(thread_id=None, app=SimpleNamespace(thread_usage=AsyncMock()))
        await shell.command(shell.registry.parse("/usage", busy=True))
        assert "No session selected" in "\n".join(block.source for block in shell.renderer.transcript.blocks.values())
        shell.backend.app.thread_usage.assert_not_awaited()
        assert shell.job is None and shell.menu_handler is None and shell.pending_codex_reset is None
        shell.renderer.transcript.close()
