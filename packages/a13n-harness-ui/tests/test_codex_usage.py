"""Subscription quota operations use only mocked transports and credentials."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx2
import pytest
from a13n_harness.model_auth import CodexCredentials
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.shell import CliShell
from a13n_harness_ui.interactive.usage import show_codex_usage
from a13n_harness_ui.model_accounts.usage import CodexUsage, CodexUsageClient, ResetRequest, ResetResult
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

pytestmark = pytest.mark.anyio


def _source():
    credentials = CodexCredentials(
        account_id="account-1",
        expires_at=datetime.now(UTC) + timedelta(hours=2),
        access_token="fixture-access",
        refresh_token="fixture-refresh",
    )
    return SimpleNamespace(load=AsyncMock(return_value=credentials), save=AsyncMock())


_USAGE = {
    "plan_type": "pro",
    "rate_limit": {
        "allowed": False,
        "limit_reached": True,
        "primary_window": {"used_percent": 100, "limit_window_seconds": 18000, "reset_at": 1900000000},
    },
}
_CREDITS = {
    "available_count": 1,
    "credits": [{"id": "credit-1", "status": "available", "reset_type": "codex_rate_limits"}],
}


async def test_usage_reads_provider_reset_time_without_redeeming() -> None:
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer fixture-access"
        assert request.headers["ChatGPT-Account-Id"] == "account-1"
        return httpx2.Response(200, json=_USAGE if request.url.path.endswith("/usage") else _CREDITS)

    source = _source()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        result = await CodexUsageClient(source, client=client).read()
    assert result.rate_limit.primary_window.reset_at == 1900000000
    assert result.reset_credits.available_count == 1
    assert [request.method for request in requests] == ["GET", "GET"]
    assert result.account_id == "account-1"
    source.save.assert_not_called()


async def test_reset_credit_failure_preserves_read_only_usage_and_redacts_body() -> None:
    def handle(request):
        return (
            httpx2.Response(200, json=_USAGE)
            if request.url.path.endswith("/usage")
            else httpx2.Response(500, text="private-token")
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        result = await CodexUsageClient(_source(), client=client).read()
    assert result.plan_type == "pro" and result.reset_credits is None
    assert "HTTP 500" in result.reset_unavailable and "private-token" not in result.reset_unavailable


@pytest.mark.parametrize("code", ["reset", "nothing_to_reset", "no_credit", "already_redeemed"])
async def test_reset_uses_exact_request_body_and_returns_known_outcomes(code: str) -> None:
    reset = ResetRequest(account_id="account-1", credit_id="credit-1", redeem_request_id=uuid4())
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.path == "/backend-api/wham/rate-limit-reset-credits/consume"
        assert json.loads(request.content) == {
            "credit_id": "credit-1",
            "redeem_request_id": str(reset.redeem_request_id),
        }
        return httpx2.Response(200, json={"code": code, "windows_reset": 2 if code == "reset" else 0})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        result = await CodexUsageClient(_source(), client=client, expected_account_id=reset.account_id).redeem(reset)
    assert result.code == code and len(requests) == 1


async def test_401_refresh_replays_same_redemption_and_saves_credentials() -> None:
    source = _source()
    refreshed = replace(await source.load(), access_token="new-access")
    refresh = AsyncMock(return_value=refreshed)
    requests = []

    def handle(request):
        requests.append(request)
        return (
            httpx2.Response(401)
            if len(requests) == 1
            else httpx2.Response(200, json={"code": "reset", "windows_reset": 1})
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        api = CodexUsageClient(source, client=client, expected_account_id="account-1", refresh=refresh)
        await api.redeem(ResetRequest(account_id="account-1", credit_id="one", redeem_request_id=uuid4()))
    assert len(requests) == 2 and requests[0].content == requests[1].content
    refresh.assert_awaited_once()
    source.save.assert_awaited_once_with(refreshed)


async def test_changed_account_blocks_reset_before_transport() -> None:
    handle = AsyncMock()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        api = CodexUsageClient(_source(), client=client, expected_account_id="other")
        with pytest.raises(Exception, match="account changed"):
            await api.redeem(ResetRequest(account_id="other", credit_id="one", redeem_request_id=uuid4()))
    handle.assert_not_called()


async def test_usage_redirect_never_forwards_credentials() -> None:
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(302, headers={"Location": "https://example.test/steal"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle), follow_redirects=True) as client:
        with pytest.raises(ValueError, match="HTTP 302"):
            await CodexUsageClient(_source(), client=client).read()
    assert len(requests) == 1


@pytest.mark.parametrize("error", [ValueError("timeout"), asyncio.CancelledError()])
async def test_uncertain_reset_survives_cancel_and_retries_same_identity(error: BaseException) -> None:
    usage = CodexUsage(account_id="account-1", **_USAGE, reset_credits=_CREDITS)
    app = SimpleNamespace(codex_usage=AsyncMock(return_value=usage), redeem_codex_reset=AsyncMock(side_effect=error))
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = SimpleNamespace(app=app, cancel=AsyncMock())
        await show_codex_usage(shell)
        await shell.menu_answer("credit-1")
        assert shell.selection.cursor == -1  # no implicit consent
        assert shell.selection.choices[0].value == "no"
        if isinstance(error, asyncio.CancelledError):
            with pytest.raises(asyncio.CancelledError):
                await shell.menu_answer("yes")
        else:
            await shell.menu_answer("yes")
        pending = shell.pending_codex_reset
        assert pending is not None
        await shell.cancel()
        app.redeem_codex_reset.side_effect = None
        app.redeem_codex_reset.return_value = ResetResult(code="already_redeemed")
        await show_codex_usage(shell)
        await shell.menu_answer("yes")
        assert [call.args[0] for call in app.redeem_codex_reset.await_args_list] == [pending, pending]
        assert shell.pending_codex_reset is None


async def test_confirmed_redemption_refresh_error_cannot_reopen_mutation() -> None:
    usage = CodexUsage(account_id="account-1", **_USAGE, reset_credits=_CREDITS)
    app = SimpleNamespace(
        codex_usage=AsyncMock(side_effect=[usage, RuntimeError("authentication unavailable")]),
        redeem_codex_reset=AsyncMock(return_value=ResetResult(code="reset", windows_reset=1)),
    )
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = SimpleNamespace(app=app, cancel=AsyncMock())
        await show_codex_usage(shell)
        await shell.menu_answer("credit-1")
        await shell.menu_answer("yes")
        assert shell.menu_handler is None and shell.pending_codex_reset is None
        app.redeem_codex_reset.assert_awaited_once()
        assert "outcome above is confirmed" in "".join(
            block.source for block in shell.renderer.transcript.blocks.values()
        )
