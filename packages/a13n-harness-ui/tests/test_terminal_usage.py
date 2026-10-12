"""Thread scope selection, accounting presentation, and the actual terminal render path."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.rendering import Status
from a13n_harness_ui.interactive.shell import CliShell, SlashCompleter
from a13n_harness_ui.interactive.theme import resolve_theme
from a13n_harness_ui.interactive.transcript import Transcript
from a13n_harness_ui.interactive.usage import thread_usage_text
from a13n_harness_ui.storage.usage import ModelUsageView, ThreadUsageView, UsageTotals
from prompt_toolkit.application import create_app_session
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth


def _totals(requests=0, input=0, output=0, cache=0, cost="0", unknown=0) -> UsageTotals:
    return UsageTotals(
        model_requests=requests,
        provider_receipts=0,
        tokens=(
            ("input_tokens", input),
            ("output_tokens", output),
            ("cache_read_tokens", cache),
            ("cache_write_tokens", 0),
        ),
        model_cost_usd=Decimal(cost),
        unknown_model_costs=unknown,
        provider_costs=(),
        unknown_provider_costs=0,
        omitted_currency_receipts=0,
    )


def _view() -> ThreadUsageView:
    root = _totals(2, 1000, 200, 600, "0.125", 1)
    child = _totals(1, 300, 50, 200, "0.25")
    combined = _totals(3, 1300, 250, 800, "0.375", 1)
    now = datetime(2026, 10, 11, tzinfo=UTC)
    return ThreadUsageView(
        thread_id="thread-selected",
        first_observed_at=now,
        observed_through=now,
        root=root,
        descendants=child,
        combined=combined,
        models=(("provider/primary", root), ("provider/worker", child)),
        other_models=_totals(),
        recent_runs=(),
        other_runs=_totals(),
        model_scopes=(
            ModelUsageView("provider/primary", root, _totals(), root),
            ModelUsageView("provider/worker", _totals(), child, child),
        ),
    )


def _render(transcript: Transcript, width: int) -> str:
    transcript.render(width)
    lines = ["".join(part[1] for part in row) for row in transcript.rows]
    assert all(get_cwidth(line) <= width for line in lines)
    return "\n".join(lines)


@pytest.mark.anyio
@pytest.mark.parametrize("model", ["openai:example", "openai-chatgpt:example"])
@pytest.mark.parametrize(
    "command,scope,total,cost,excluded",
    [
        ("/usage", "All", "1,550", "$0.3750+", None),
        ("/usage all", "All", "1,550", "$0.3750+", None),
        ("/usage root", "Root", "1,200", "$0.1250+", "provider/worker"),
        ("/usage subagents", "Subagents", "350", "$0.2500", "provider/primary"),
        ("/usage details", "All", "1,550", "$0.375", None),
    ],
)
async def test_usage_reads_selected_thread_while_busy_without_subscription_or_model_calls(
    model, command, scope, total, cost, excluded
):
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest(), status=Status(model=model))
        app = SimpleNamespace(thread_usage=AsyncMock(return_value=_view()), codex_usage=AsyncMock())
        shell.backend = SimpleNamespace(thread_id="thread-selected", app=app)
        shell.job = asyncio.create_task(asyncio.Event().wait())
        shell.composer.text = "keep this draft"
        try:
            assert shell.busy
            invocation = shell.registry.parse(command, busy=True)
            assert invocation is not None
            await shell.command(invocation)
            app.thread_usage.assert_awaited_once_with(thread_id="thread-selected")
            app.codex_usage.assert_not_awaited()
            block = next(iter(shell.renderer.transcript.blocks.values()))
            assert block.kind == "info" and block.markdown
            text = _render(shell.renderer.transcript, 100)
            assert f"Thread usage · {scope}" in text
            assert f"{total} tokens" in text and cost in text
            if excluded:
                assert excluded not in text
            assert ("Recent Runs" in text) == (command == "/usage details")
            assert shell.composer.text == "keep this draft"
            assert shell.menu_handler is None and shell.pending_codex_reset is None
        finally:
            shell.job.cancel()
            await asyncio.gather(shell.job, return_exceptions=True)
            shell.renderer.transcript.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_usage_reflows_long_literal_model_names_and_exact_counters_on_resize(theme):
    name = "provider/模型_[preview]*<tag>`" + "long-model-name-" * 5
    view = _view()
    view = replace(view, model_scopes=(replace(view.model_scopes[0], name=name), view.model_scopes[1]))
    transcript = Transcript()
    transcript.theme = resolve_theme(theme)
    try:
        transcript.append(thread_usage_text(view), markdown=True, kind="info")
        for width in (120, 80, 40, 24, 80):
            text = _render(transcript, width)
            unfolded = "".join(text.replace("│", "").split())
            assert name in unfolded
            assert "1,550tokens" in unfolded and "Input1,300" in unfolded and "Output250" in unfolded
            assert "Cacheread800(51.6%)" in unfolded
            assert "Cachewrite0" in unfolded and "$0.3750+" in unfolded
            assert "/usagesubagents" in unfolded and "/usagedetails" in unfolded
            assert any("bold" in style for row in transcript.rows for style, _ in row)
        assert "Model | Requests" not in text
    finally:
        transcript.close()


@pytest.mark.parametrize(
    "cost,unknown,expected",
    [
        ("0", 2, "unknown"),
        ("0", 0, "$0.0000"),
        ("0.0000009", 0, "<$0.0001"),
        ("0.125", 1, "$0.1250+"),
        ("1234.56", 0, "$1,234.56"),
    ],
)
def test_model_cost_distinguishes_unknown_free_tiny_and_partial(cost, unknown, expected):
    view = _view()
    totals = replace(view.root, model_cost_usd=Decimal(cost), unknown_model_costs=unknown)
    view = replace(view, root=totals, model_scopes=(), models=())
    text = thread_usage_text(view, scope="root")
    assert f"**Cost {expected}**" in text
    assert ("unknown-cost responses" in text) == bool(unknown)
    if unknown == 2:
        assert "$0.0000" not in text


def test_legacy_scope_attribution_is_not_borrowed_from_combined_and_other_models_are_not_doubled():
    view = replace(_view(), model_scopes=())
    assert "provider/primary" in thread_usage_text(view)
    root = thread_usage_text(view, scope="root")
    assert "1,200 tokens" in root
    assert "attribution is unavailable" in root and "provider/worker" not in root
    overflow = replace(view, other_models=view.root)
    assert thread_usage_text(overflow).count("**Other models**") == 1
    overflow = replace(
        overflow, model_scopes=(*_view().model_scopes, ModelUsageView("Other models", view.root, _totals(), view.root))
    )
    assert thread_usage_text(overflow).count("**Other models**") == 1
    assert "Other models" not in thread_usage_text(overflow, scope="subagents")


def test_empty_coverage_empty_scope_and_non_model_provider_costs_remain_distinct():
    view = _view()
    empty = replace(view, first_observed_at=None)
    assert "unavailable, not proven zero" in thread_usage_text(empty)
    assert "$0.0000" not in thread_usage_text(empty)
    providers = replace(
        _totals(),
        provider_receipts=3,
        provider_costs=(("EUR", Decimal("0.2")),),
        unknown_provider_costs=1,
        omitted_currency_receipts=1,
    )
    view = replace(view, descendants=providers, model_scopes=(view.model_scopes[0],))
    text = thread_usage_text(view, scope="subagents")
    assert "No recorded model responses" in text
    assert "provider/worker" not in text
    assert "EUR 0.2 known subtotal" in text and "currencies are not converted" in text
    assert "1 unknown-cost receipts" in text and "1 currency entries omitted" in text
    assert "$0.0000" not in text
    assert "Non-model providers" not in thread_usage_text(view, scope="root")


def test_nonzero_audio_and_diagnostic_exact_cost_are_retained_without_changing_token_total():
    view = _view()
    tokens = (
        *view.combined.tokens,
        ("input_audio_tokens", 12),
        ("output_audio_tokens", 8),
        ("cache_audio_read_tokens", 5),
    )
    view = replace(view, combined=replace(view.combined, tokens=tokens, model_cost_usd=Decimal("0.0000009")))
    text = thread_usage_text(view)
    assert "1,550 tokens" in text
    assert "Audio input 12 · output 8 · cached 5" in text
    details = thread_usage_text(view, details=True)
    assert "$0.0000009+" in details
    assert "Thread: thread-selected" in details and "Observed through:" in details


def test_scope_command_completion_is_available_during_work():
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        try:
            completer = SlashCompleter(shell.registry)
            choices = {item.text.strip() for item in completer.get_completions(Document("/usage "), CompleteEvent())}
            assert {"all", "root", "subagents", "details", "subscription", "reset"} <= choices
            with pytest.raises(ValueError, match="Choices"):
                shell.registry.parse("/usage invalid", busy=True)
        finally:
            shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_failed_usage_read_does_not_substitute_status_totals_or_query_subscription():
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest(), status=Status(model="openai-codex:example"))
        shell.status.restore_usage(_view().combined)
        app = SimpleNamespace(
            thread_usage=AsyncMock(side_effect=ValueError("Usage unavailable")), codex_usage=AsyncMock()
        )
        shell.backend = SimpleNamespace(thread_id="thread-selected", app=app)
        try:
            with pytest.raises(ValueError, match="Usage unavailable"):
                await shell.command(shell.registry.parse("/usage"))
            assert not shell.renderer.transcript.blocks
            app.codex_usage.assert_not_awaited()
            assert shell.job is None and shell.menu_handler is None and shell.pending_codex_reset is None
        finally:
            shell.renderer.transcript.close()
