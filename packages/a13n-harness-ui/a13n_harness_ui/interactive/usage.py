"""Read-only quota display and explicit, account-bound reset confirmation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic_ai.exceptions import UserError

from a13n_harness_ui.model_accounts.usage import CodexUsage, ResetRequest
from a13n_harness_ui.storage.usage import ThreadUsageView, UsageTotals

from .selection import Choice

if TYPE_CHECKING:
    from .shell import CliShell


def thread_usage_text(view: ThreadUsageView) -> str:
    lines = [f"Thread usage · {view.thread_id}"]
    if view.first_observed_at is None:
        return "\n".join([*lines, "No recorded usage yet. Lifetime coverage is unavailable, not proven zero."])
    lines.extend(
        [
            f"Observed {view.first_observed_at.astimezone():%Y-%m-%d %H:%M:%S %Z}"
            f" through {view.observed_through.astimezone():%Y-%m-%d %H:%M:%S %Z}"
            if view.observed_through
            else "",
            "Recorded-so-far only; pre-ledger or unobserved usage is unavailable. Not a provider invoice.",
        ]
    )
    for title, totals in (
        ("Root agent", view.root),
        ("Descendants (inline + async)", view.descendants),
        ("Combined", view.combined),
    ):
        lines.extend(_total_lines(title, totals))
    lines.append("By model (root + descendants):")
    for name, totals in view.models:
        lines.extend(_total_lines(name, totals, details=False))
    if view.other_models.model_requests:
        lines.extend(_total_lines("Other models (breakdown limited to 32 names)", view.other_models, details=False))
    lines.append("Recent Runs (up to 32, newest observed first; unique contributions):")
    for run in view.recent_runs:
        role = "child" if run.descendant else "root"
        lines.extend(_total_lines(f"{run.run_id} · {role} · {run.agent_instance_id}", run.totals))
    if view.other_runs.model_requests or view.other_runs.provider_receipts:
        lines.extend(_total_lines("Older Runs (included in Thread totals)", view.other_runs))
    lines.extend(
        [
            "Cache/audio counters are subsets of input/output, not extra tokens.",
            "Cache rate = cache-read / (input + output), matching the status bar.",
            "Provider receipts are deduplicated across Runs and attributed to first observation.",
            "Context occupancy: /status. Codex subscription limits: /usage subscription; reset credits: /usage reset.",
        ]
    )
    return "\n".join(lines)


def _total_lines(title: str, totals: UsageTotals, *, details: bool = True) -> list[str]:
    tokens = dict(totals.tokens)
    total = tokens["input_tokens"] + tokens["output_tokens"]
    cache_rate = f"{100 * tokens['cache_read_tokens'] / total:.1f}%" if total else "--"
    lines = [
        f"{title}: {totals.model_requests:,} model responses · {total:,} tokens"
        f" (in {tokens['input_tokens']:,} / out {tokens['output_tokens']:,})",
        f"  Model cost: USD {totals.model_cost_usd:.6f} known subtotal · {totals.unknown_model_costs:,} unknown-cost responses",
    ]
    if details:
        lines.extend(
            [
                f"  Cache: read {tokens['cache_read_tokens']:,} / write {tokens['cache_write_tokens']:,} · rate {cache_rate}",
                f"  Audio: in {tokens['input_audio_tokens']:,} / out {tokens['output_audio_tokens']:,} / cached {tokens['cache_audio_read_tokens']:,}",
                f"  Provider receipts: {totals.provider_receipts:,} · {totals.unknown_provider_costs:,} unknown-cost receipts",
            ]
        )
        for currency, cost in totals.provider_costs:
            lines.append(f"    {currency} {cost} known provider subtotal (separate from model costs)")
        if totals.omitted_currency_receipts:
            lines.append(
                f"    {totals.omitted_currency_receipts:,} receipts omitted from currency breakdown (32-currency limit)"
            )
    return lines


def usage_text(usage: CodexUsage, *, now: datetime | None = None) -> str:
    now = (now or datetime.now(UTC)).astimezone()
    lines = [f"Codex subscription · {usage.plan_type} · account {usage.account_id}"]
    limits = usage.rate_limit
    if limits is None or (limits.primary_window is None and limits.secondary_window is None):
        lines.append("Usage windows unavailable.")
    if limits is not None:
        if limits.limit_reached or not limits.allowed:
            lines.append("Subscription limit reached or access currently unavailable.")
        for fallback, window in (("Primary", limits.primary_window), ("Secondary", limits.secondary_window)):
            if window is None:
                continue
            name = next(
                (
                    label
                    for seconds, label in ((18000, "5h"), (86400, "Daily"), (604800, "Weekly"), (2592000, "Monthly"))
                    if abs(window.limit_window_seconds - seconds) <= seconds * 0.05
                ),
                f"{fallback} ({window.limit_window_seconds // 60}m)",
            )
            remaining = max(0, min(100, 100 - window.used_percent))
            reset = datetime.fromtimestamp(window.reset_at, UTC).astimezone()
            reset_text = reset.strftime("%H:%M" if reset.date() == now.date() else "%H:%M on %d %b %Y")
            filled = round(remaining / 10)
            lines.append(
                f"{name} limit: {remaining}% remaining · [{'=' * filled}{'-' * (10 - filled)}] · resets {reset_text}"
            )
    if usage.reset_credits is not None:
        lines.append(
            f"Reset credits available: {usage.reset_credits.available_count} · /usage reset to review (consumes a credit)."
        )
    if usage.reset_unavailable:
        lines.append(f"Reset credits unavailable: {usage.reset_unavailable}")
    return "\n".join(lines)


async def show_codex_usage(shell: CliShell) -> str:
    """Status never takes over the composer, including with an uncertain redemption."""
    assert shell.backend is not None
    shell.emit(usage_text(await shell.backend.app.codex_usage()))
    if shell.pending_codex_reset is not None:
        shell.emit(
            f"Reset outcome is unconfirmed. Redemption ID: {shell.pending_codex_reset.redeem_request_id}. "
            "Use /usage reset to explicitly retry with the same ID."
        )
    return ""


async def choose_codex_reset(shell: CliShell) -> str:
    assert shell.backend is not None
    app = shell.backend.app
    if shell.pending_codex_reset is not None:
        _confirm_reset(
            shell, shell.pending_codex_reset, "Previous outcome is unknown; retry uses the same redemption ID."
        )
        return ""
    usage = await app.codex_usage()
    shell.emit(usage_text(usage))
    credits = (
        tuple(
            item
            for item in usage.reset_credits.credits
            if item.status == "available" and item.reset_type == "codex_rate_limits"
        )
        if usage.reset_credits is not None and usage.reset_credits.available_count > 0
        else ()
    )

    async def selected(value: str | tuple[str, ...]) -> None:
        if value == "close":
            return
        if value == "refresh":
            await choose_codex_reset(shell)
            return
        credit = next(item for item in credits if item.id == value)
        request = ResetRequest(account_id=usage.account_id, credit_id=credit.id, redeem_request_id=uuid4())

        _confirm_reset(
            shell,
            request,
            f"{credit.description or credit.title or ''} Expires: {credit.expires_at or 'not reported'}.",
        )

    if not credits:
        shell.emit("No eligible reset credits available. /status refreshes usage without redeeming.")
        return ""
    shell.open_menu(
        "Codex reset credits — select a credit to review; nothing is redeemed yet",
        (
            Choice("close", "Close"),
            Choice("refresh", "Refresh usage"),
            *(
                Choice(
                    credit.id,
                    credit.title or "Redeem usage limit reset",
                    credit.description or "Uses one available reset credit",
                )
                for credit in credits
            ),
        ),
        selected,
    )
    return ""


def _confirm_reset(shell: CliShell, request: ResetRequest, detail: str) -> None:
    async def confirm(answer: str | tuple[str, ...]) -> None:
        if answer != "yes":
            return
        assert shell.backend is not None
        # Preserve the operation outside the menu, including after Ctrl+C.
        was_pending = shell.pending_codex_reset is not None
        shell.pending_codex_reset = request
        try:
            result = await shell.backend.app.redeem_codex_reset(request)
        except UserError as exc:
            # Account/configuration rejection happened before this submission.
            # It cannot erase an earlier attempt with an unknown outcome.
            if not was_pending:
                shell.pending_codex_reset = None
            shell.emit(f"Reset not sent: {exc}. Use /usage reset to continue.")
            return
        except (Exception, asyncio.CancelledError):
            shell.emit(
                f"Reset outcome is unconfirmed. Redemption ID: {request.redeem_request_id}. "
                "Use /usage reset in this terminal to retry with the same ID; do not start a new redemption."
            )
            raise
        shell.pending_codex_reset = None
        shell.emit(
            f"Codex reset: {result.code} · {result.windows_reset} windows reset. Redemption ID: {request.redeem_request_id}"
        )
        # The mutation is finished. A later read failure must never restore its
        # confirmation handler, even for authentication errors or cancellation.
        try:
            await show_codex_usage(shell)
        except asyncio.CancelledError:
            shell.emit("Redemption is confirmed; usage refresh cancelled. /status refreshes without redeeming again.")
            raise
        except Exception as exc:
            shell.emit(
                f"Redemption outcome above is confirmed; usage refresh failed: {exc}. /status refreshes without redeeming again."
            )

    shell.open_menu(
        f"Use one reset credit for account {request.account_id}? This consumes the selected entitlement. "
        f"{detail} Redemption ID: {request.redeem_request_id}",
        (Choice("no", "No, go back"), Choice("yes", "Yes, use this reset credit")),
        confirm,
        explicit=True,
    )
