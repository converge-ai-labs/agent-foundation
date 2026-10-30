"""Thread usage presentation and separate, explicitly confirmed subscription operations."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from a13n_harness.providers.model.openai_chatgpt import DEFINITION as CHATGPT_DEFINITION
from pydantic_ai.exceptions import UserError

from a13n_harness_ui.model_accounts.provider_usage import GrokUsage
from a13n_harness_ui.model_accounts.subscriptions import SubscriptionAccountUpdate, SubscriptionCatalog
from a13n_harness_ui.model_accounts.usage import CodexUsage, ResetRequest
from a13n_harness_ui.storage.usage import ModelUsageView, ThreadUsageView, UsageTotals

from .selection import Choice

if TYPE_CHECKING:
    from .shell import CliShell


type UsageScope = Literal["all", "root", "subagents"]


def thread_usage_text(view: ThreadUsageView, *, scope: UsageScope = "all", details: bool = False) -> str:
    """Markdown panel body; model sections reflow instead of squeezing a wide table."""
    title = {"all": "All", "root": "Root", "subagents": "Subagents"}[scope]
    lines = [f"Thread usage · {title}", "Recorded so far · auxiliary models included", ""]
    if view.first_observed_at is None:
        lines.extend(["No recorded usage yet. Historical coverage is unavailable, not proven zero.", ""])
    else:
        totals = _scope_totals(view, scope)
        lines.extend(_total_lines(totals, exact=details))
        if scope == "all":
            lines.extend(
                [f"Root {view.root.model_requests:,} / Subagents {view.descendants.model_requests:,} responses", ""]
            )
        lines.extend(_provider_lines(totals))
        lines.extend(["### By model", ""])
        models = _scope_models(view, scope)
        for name, model in models:
            lines.extend([f"**{_literal(name)}**  ", *_total_lines(model, exact=details)])
        if not models:
            lines.extend(
                [
                    "Model attribution is unavailable for this scope in older data."
                    if totals.model_requests
                    else "No recorded model responses in this scope.",
                    "",
                ]
            )
        if details:
            lines.extend(_diagnostic_lines(view))
        lines.extend(
            [
                "Cache/audio are included in input/output, not extra tokens. "
                "Model costs are USD estimates; + means a partial known subtotal.",
                "",
            ]
        )
    lines.append("Scopes: `/usage all` · `/usage root` · `/usage subagents`")
    if not details:
        lines[-1] += "  "
        lines.append("Attribution and Runs: `/usage details`")
    return "\n".join(lines)


def _scope_totals(view: ThreadUsageView | ModelUsageView, scope: UsageScope) -> UsageTotals:
    return view.root if scope == "root" else view.descendants if scope == "subagents" else view.combined


def _scope_models(view: ThreadUsageView, scope: UsageScope) -> tuple[tuple[str, UsageTotals], ...]:
    if view.model_scopes:
        return tuple(
            (model.name, totals)
            for model in view.model_scopes
            if (totals := _scope_totals(model, scope)).model_requests
        )
    # Legacy projections know combined attribution only; never borrow it for a single scope.
    if scope != "all":
        return ()
    return view.models + ((("Other models", view.other_models),) if view.other_models.model_requests else ())


def _literal(value: str) -> str:
    """Keep provider/model/source labels literal inside generated Markdown."""
    return "".join("\\" + char if char in "\\`*_[]<>!&#" else char for char in " ".join(value.split()))


def _model_cost(totals: UsageTotals, *, exact: bool = False) -> str:
    if not totals.model_requests:
        return "—"
    if totals.model_requests == totals.unknown_model_costs:
        return "unknown"
    cost = totals.model_cost_usd
    if exact:
        amount = f"${cost:f}"
    elif 0 < cost < Decimal("0.0001"):
        amount = "<$0.0001"
    else:
        amount = f"${cost:,.4f}" if cost < 1 else f"${cost:,.2f}"
    return amount + ("+" if totals.unknown_model_costs else "")


def _total_lines(totals: UsageTotals, *, exact: bool = False) -> list[str]:
    if not totals.model_requests:
        return ["No recorded model responses in this scope.", ""]
    tokens = dict(totals.tokens)
    input_tokens, output_tokens = tokens.get("input_tokens", 0), tokens.get("output_tokens", 0)
    total = input_tokens + output_tokens
    cache = tokens.get("cache_read_tokens", 0)
    cost = _model_cost(totals, exact=exact)
    rows = [
        f"**{total:,} tokens** · **Cost {cost}** · {totals.model_requests:,} responses",
        f"Input {input_tokens:,} · Output {output_tokens:,}",
        f"Cache read {cache:,}"
        + (f" ({100 * cache / total:.1f}%)" if total else "")
        + f" · Cache write {tokens.get('cache_write_tokens', 0):,}",
    ]
    audio = [
        (label, tokens.get(key, 0))
        for label, key in (
            ("input", "input_audio_tokens"),
            ("output", "output_audio_tokens"),
            ("cached", "cache_audio_read_tokens"),
        )
    ]
    if any(count for _, count in audio):
        rows.append("Audio " + " · ".join(f"{label} {count:,}" for label, count in audio if count))
    if totals.unknown_model_costs:
        rows.append(f"{totals.unknown_model_costs:,} unknown-cost responses")
    return ["  \n".join(rows), ""]


def _provider_lines(totals: UsageTotals) -> list[str]:
    if not totals.provider_receipts:
        return []
    rows = [f"{totals.provider_receipts:,} receipts · separate from model costs; currencies are not converted."]
    rows.extend(f"{_literal(currency)} {cost} known subtotal" for currency, cost in totals.provider_costs)
    if totals.unknown_provider_costs:
        rows.append(f"{totals.unknown_provider_costs:,} unknown-cost receipts")
    if totals.omitted_currency_receipts:
        rows.append(f"{totals.omitted_currency_receipts:,} currency entries omitted; subtotal incomplete")
    return ["### Non-model providers", "", "  \n".join(rows), ""]


def _diagnostic_lines(view: ThreadUsageView) -> list[str]:
    lines = ["### Coverage", "", f"Thread: {_literal(view.thread_id)}  "]
    if view.first_observed_at is not None:
        lines.append(f"First observed: {view.first_observed_at.astimezone():%Y-%m-%d %H:%M:%S %Z}  ")
    if view.observed_through is not None:
        lines.append(f"Observed through: {view.observed_through.astimezone():%Y-%m-%d %H:%M:%S %Z}  ")
    lines.extend(["Not a provider invoice. Missing history and prices are not backfilled.", ""])
    scopes: tuple[UsageScope, ...] = ("root", "subagents")
    for scope in scopes:
        title = "Root" if scope == "root" else "Subagents"
        totals = _scope_totals(view, scope)
        lines.extend([f"### {title}", "", *_total_lines(totals, exact=True), *_provider_lines(totals)])
        for name, totals in _scope_models(view, scope):
            lines.extend([f"**{_literal(name)}**", "", *_total_lines(totals, exact=True)])
    if view.groups:
        lines.extend(["### Agents and sources", "", "Overlapping breakdowns, not additional usage.", ""])
    for group in view.groups:
        role = "Subagent" if group.descendant else "Root"
        source = {"agent": "Agent requests", "files.media_understanding": "Media understanding · view"}.get(
            group.source, group.source or "Unattributed"
        )
        lines.extend(
            [
                f"**{_literal(group.model)}** · {role} · {_literal(source)}  ",
                _literal(group.agent_instance_id),
                "",
                *_total_lines(group.totals, exact=True),
            ]
        )
    if view.other_groups is not None and view.other_groups.model_requests:
        lines.extend(
            ["**Other agent/source groups (included in totals)**", "", *_total_lines(view.other_groups, exact=True)]
        )
    lines.extend(["### Recent Runs", "", "Up to 32, newest observed first; unique contributions.", ""])
    for run in view.recent_runs:
        role = "Subagent" if run.descendant else "Root"
        lines.extend(
            [
                f"**{_literal(run.run_id)}** · {role}  ",
                _literal(run.agent_instance_id),
                "",
                *_total_lines(run.totals, exact=True),
                *_provider_lines(run.totals),
            ]
        )
    if view.other_runs.model_requests or view.other_runs.provider_receipts:
        lines.extend(
            [
                "**Older Runs (included in Thread totals)**",
                "",
                *_total_lines(view.other_runs, exact=True),
                *_provider_lines(view.other_runs),
            ]
        )
    lines.extend(
        [
            "Cache rate = cache-read / (input + output), matching the status bar. "
            "Provider receipts are deduplicated across Runs and attributed to first observation.",
            "",
            "Context: `/status` · Subscription: `/usage subscription` · Codex credits: `/usage reset`",
            "",
        ]
    )
    return lines


def window_label(seconds: int, fallback: str) -> str:
    return {18000: "5h", 86400: "Daily", 604800: "Weekly", 2592000: "30-day"}.get(
        seconds, f"{fallback} ({seconds // 60}m)"
    )


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
            name = window_label(window.limit_window_seconds, fallback)
            remaining = max(0, min(100, 100 - window.used_percent))
            reset = datetime.fromtimestamp(window.reset_at, UTC).astimezone()
            reset_text = reset.strftime("%H:%M" if reset.date() == now.date() else "%H:%M on %d %b %Y")
            filled = round(remaining / 10)
            lines.append(
                f"{name} limit: {remaining:g}% remaining · [{'=' * filled}{'-' * (10 - filled)}] · resets {reset_text}"
            )
    if usage.reset_credits is not None:
        lines.append(f"Reset credits: {usage.reset_credits.available_count} · /usage reset")
    if usage.reset_unavailable:
        lines.append(f"Reset credits unavailable: {usage.reset_unavailable}")
    return "\n".join(lines)


async def show_subscription_usage(shell: CliShell) -> str:
    assert shell.backend is not None
    if shell.status.model.startswith("openai-chatgpt:"):
        shell.emit(
            "ChatGPT subscription usage is unavailable in Harness UI.\n"
            f"Manage usage in ChatGPT: {CHATGPT_DEFINITION.setup_url}",
            kind="info",
        )
        return ""
    shell.emit(subscription_text(await shell.backend.app.subscriptions()), kind="info")
    return ""


def subscription_text(catalog: SubscriptionCatalog) -> str:
    lines = ["Managed subscription accounts · request-driven updates · 10-minute idle refresh", ""]
    names = {view.account.id: view.account.name for view in catalog.accounts}
    for view in catalog.accounts:
        account = view.account
        state = "paused" if not account.enabled else view.authentication.availability.value
        usage = view.usage.usage
        windows = usage.rate_limit if isinstance(usage, CodexUsage) else None
        remaining = (
            " · ".join(
                window_label(window.limit_window_seconds, label)
                + f" {max(0, min(100, 100 - window.used_percent)):g}% left · resets "
                + datetime.fromtimestamp(window.reset_at, UTC).astimezone().strftime("%d %b %H:%M %Z")
                for label, window in (("Primary", windows.primary_window), ("Secondary", windows.secondary_window))
                if window is not None
            )
            if windows and (windows.primary_window is not None or windows.secondary_window is not None)
            else "Usage unknown"
        )
        if isinstance(usage, GrokUsage) and usage.config is not None:
            billing = usage.config
            percent = billing.usage_percent
            remaining = (
                f"Allowance {max(0, min(100, 100 - percent)):g}% left" if percent is not None else "Allowance unknown"
            )
            reset = (billing.current_period.end if billing.current_period else None) or billing.billing_period_end
            if reset:
                remaining += f" · resets {reset}"
            if billing.prepaid_balance is not None:
                remaining += f" · Purchased credits ${abs(billing.prepaid_balance.val) / 100:.2f}"
        credits = usage.credits if isinstance(usage, CodexUsage) else None
        balance = f" · Credits {credits.balance or ('available' if credits.has_credits else 'none')}" if credits else ""
        observed = (
            datetime.fromtimestamp(view.usage.observed_at, UTC).astimezone().strftime("%H:%M:%S")
            if view.usage.observed_at
            else "never"
        )
        lines.append(f"{account.name} · {account.provider} · {state} · {remaining}{balance} · updated {observed}")
        if isinstance(usage, CodexUsage):
            vouchers = usage.reset_credits or usage.rate_limit_reset_credits
            if vouchers is not None:
                lines.append(f"  Reset vouchers: {vouchers.available_count} available")
        if view.usage.error:
            lines.append(f"  Refresh failed: {view.usage.error}")
    lines.extend(["", "Account groups"])
    for group in catalog.groups:
        members = ", ".join(
            names.get(member.account, "Deleted account")
            + (f" x{member.weight}" if group.strategy == "weighted" else "")
            for member in group.accounts
        )
        lines.append(
            f"{group.name} · {group.strategy} · {'credits allowed' if group.allow_credits else 'included only'} · {members or 'no members'}"
        )
    lines.extend(
        [
            "",
            "/usage accounts: refresh or pause an account · /usage reset: select account and voucher",
            "Manage group membership and logins in WebUI Accounts & API keys.",
        ]
    )
    return "\n".join(lines)


async def choose_subscription_account(shell: CliShell, *, reset: bool = False) -> str:
    assert shell.backend is not None
    app = shell.backend.app
    catalog = await app.subscriptions()

    async def selected(value: str | tuple[str, ...]) -> None:
        if value == "close":
            return
        account = next(view.account for view in catalog.accounts if view.account.id == value)
        if reset:
            await choose_codex_reset(shell, account_id=account.id)
            return

        async def action(value: str | tuple[str, ...]) -> None:
            if value == "refresh":
                await app.refresh_subscription_usage(account.id)
            elif value == "toggle":
                await app.update_subscription_account(
                    account.id, SubscriptionAccountUpdate(enabled=not account.enabled)
                )
            else:
                return
            await show_subscription_usage(shell)

        shell.open_menu(
            account.name,
            (
                Choice("close", "Close"),
                Choice("refresh", "Refresh usage now"),
                Choice("toggle", "Pause account" if account.enabled else "Enable account"),
            ),
            action,
        )

    shell.open_menu(
        "Select an account for reset vouchers" if reset else "Managed accounts",
        (
            Choice("close", "Close"),
            *(
                Choice(view.account.id, view.account.name, view.account.provider)
                for view in catalog.accounts
                if not reset or view.account.provider == "codex"
            ),
        ),
        selected,
    )
    return ""


async def show_codex_usage(shell: CliShell, account_id: str = "acct-codex-default") -> str:
    """Status never takes over the composer, including with an uncertain redemption."""
    assert shell.backend is not None
    shell.emit(usage_text(await shell.backend.app.codex_usage(account_id)), kind="info")
    if shell.pending_codex_reset is not None:
        shell.emit(
            f"Reset outcome is unconfirmed. Redemption ID: {shell.pending_codex_reset.redeem_request_id}. "
            "Use /usage reset to explicitly retry with the same ID."
        )
    return ""


async def choose_codex_reset(shell: CliShell, *, account_id: str | None = None) -> str:
    assert shell.backend is not None
    app = shell.backend.app
    if shell.pending_codex_reset is not None:
        _confirm_reset(
            shell,
            shell.pending_codex_reset,
            "Previous outcome is unknown; retry uses the same redemption ID.",
            account_id=shell.pending_codex_account,
        )
        return ""
    if account_id is None:
        return await choose_subscription_account(shell, reset=True)
    usage = await app.codex_usage(account_id)
    shell.emit(usage_text(usage), kind="info")
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
            await choose_codex_reset(shell, account_id=account_id)
            return
        credit = next(item for item in credits if item.id == value)
        request = ResetRequest(account_id=usage.account_id, credit_id=credit.id, redeem_request_id=uuid4())

        _confirm_reset(
            shell,
            request,
            f"{credit.description or credit.title or ''} Expires: {credit.expires_at or 'not reported'}.",
            account_id=account_id,
        )

    if not credits:
        shell.emit("No eligible reset credits available. /usage accounts offers manual refresh without redeeming.")
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


def _confirm_reset(shell: CliShell, request: ResetRequest, detail: str, *, account_id: str) -> None:
    async def confirm(answer: str | tuple[str, ...]) -> None:
        if answer != "yes":
            return
        assert shell.backend is not None
        # Preserve the operation outside the menu, including after Ctrl+C.
        was_pending = shell.pending_codex_reset is not None
        shell.pending_codex_reset = request
        shell.pending_codex_account = account_id
        try:
            result = await shell.backend.app.redeem_codex_reset(request, account_id=account_id)
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
            await show_codex_usage(shell, account_id)
        except asyncio.CancelledError:
            shell.emit(
                "Redemption is confirmed; usage refresh cancelled. /usage accounts offers manual refresh without redeeming again."
            )
            raise
        except Exception as exc:
            shell.emit(
                f"Redemption outcome above is confirmed; usage refresh failed: {exc}. /usage accounts offers manual refresh without redeeming again."
            )

    shell.open_menu(
        f"Use one reset credit for account {request.account_id}? This consumes the selected entitlement. "
        f"{detail} Redemption ID: {request.redeem_request_id}",
        (Choice("no", "No, go back"), Choice("yes", "Yes, use this reset credit")),
        confirm,
        explicit=True,
    )
