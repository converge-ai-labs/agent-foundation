"""Read-only quota display and explicit, account-bound reset confirmation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic_ai.exceptions import UserError

from a13n_harness_ui.model_accounts.usage import CodexUsage, ResetRequest

from .selection import Choice

if TYPE_CHECKING:
    from .shell import CliShell


def usage_text(usage: CodexUsage) -> str:
    lines = [f"Codex subscription · {usage.plan_type} · account {usage.account_id}"]
    if usage.rate_limit is None:
        lines.append("Usage windows unavailable.")
    else:
        for name, window in (
            ("Primary", usage.rate_limit.primary_window),
            ("Secondary", usage.rate_limit.secondary_window),
        ):
            if window is not None:
                reset = datetime.fromtimestamp(window.reset_at, UTC).astimezone().isoformat(timespec="minutes")
                lines.append(
                    f"{name}: {window.used_percent}% used · {window.limit_window_seconds // 60} minute window · resets {reset}"
                )
    if usage.reset_credits is not None:
        lines.append(
            f"Reset credits available: {usage.reset_credits.available_count}. Redemption consumes a credit; refreshing does not."
        )
    if usage.reset_unavailable:
        lines.append(f"Reset credits unavailable: {usage.reset_unavailable}")
    return "\n".join(lines)


async def show_codex_usage(shell: CliShell) -> str:
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
            await show_codex_usage(shell)
            return
        credit = next(item for item in credits if item.id == value)
        request = ResetRequest(account_id=usage.account_id, credit_id=credit.id, redeem_request_id=uuid4())

        _confirm_reset(
            shell,
            request,
            f"{credit.description or credit.title or ''} Expires: {credit.expires_at or 'not reported'}.",
        )

    shell.open_menu(
        "Codex usage — refresh is read-only; resetting consumes a credit",
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
            shell.emit(f"Reset not sent: {exc}. Use /status to continue.")
            return
        except (Exception, asyncio.CancelledError):
            shell.emit(
                f"Reset outcome is unconfirmed. Redemption ID: {request.redeem_request_id}. "
                "Use /status in this terminal to retry with the same ID; do not start a new redemption."
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
