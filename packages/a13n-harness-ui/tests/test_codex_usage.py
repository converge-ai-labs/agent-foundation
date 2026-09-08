"""Subscription quota operations use only mocked transports and credentials."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx2
import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.shell import CliShell
from a13n_harness_ui.interactive.usage import choose_codex_reset, usage_text
from a13n_harness_ui.model_accounts.usage import CodexUsage, CodexUsageClient, ResetRequest, ResetResult
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from pydantic_ai.providers.openai_codex import OpenAICodexCredentials

pytestmark = pytest.mark.anyio


def _source():
    credentials = OpenAICodexCredentials(
        account_id="account-1",
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
    token_requests = []
    requests = []

    def handle(request):
        if request.url.path == "/oauth/token":
            token_requests.append(request)
            return httpx2.Response(
                200, json={"access_token": refreshed.access_token, "refresh_token": refreshed.refresh_token}
            )
        requests.append(request)
        return (
            httpx2.Response(401)
            if len(requests) == 1
            else httpx2.Response(200, json={"code": "reset", "windows_reset": 1})
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        api = CodexUsageClient(source, client=client, expected_account_id="account-1")
        await api.redeem(ResetRequest(account_id="account-1", credit_id="one", redeem_request_id=uuid4()))
    assert len(requests) == 2 and requests[0].content == requests[1].content
    assert len(token_requests) == 1
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
        await choose_codex_reset(shell)
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
        await choose_codex_reset(shell)
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
        await choose_codex_reset(shell)
        await shell.menu_answer("credit-1")
        await shell.menu_answer("yes")
        assert shell.menu_handler is None and shell.pending_codex_reset is None
        app.redeem_codex_reset.assert_awaited_once()
        assert "outcome above is confirmed" in "".join(
            block.source for block in shell.renderer.transcript.blocks.values()
        )


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("credits", [None, _CREDITS, {"available_count": 0}])
async def test_status_usage_is_nonmodal_even_with_pending_redemption(pending, credits) -> None:
    usage = CodexUsage(account_id="account-1", **_USAGE, reset_credits=credits)
    app = SimpleNamespace(codex_usage=AsyncMock(return_value=usage), redeem_codex_reset=AsyncMock())
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = SimpleNamespace(app=app)
        shell.status.model = "openai-codex:fixture"
        shell.composer.text = "keep this draft"
        if pending:
            shell.pending_codex_reset = ResetRequest(
                account_id="account-1", credit_id="credit-1", redeem_request_id=uuid4()
            )
        await shell.command(shell.registry.parse("/status"))
        await shell.job
        assert shell.menu_handler is None and shell.selection is None
        assert shell.composer.text == "keep this draft"
        app.redeem_codex_reset.assert_not_awaited()
        text = "".join(block.source for block in shell.renderer.transcript.blocks.values())
        assert "0% remaining" in text
        if pending:
            assert "/usage reset" in text


@pytest.mark.parametrize("seconds,label", [(18000, "5h"), (604800, "Weekly"), (86400, "Daily"), (420, "Primary (7m)")])
@pytest.mark.parametrize("used,remaining", [(42, 58), (-5, 100), (150, 0)])
async def test_usage_remaining_window_labels(seconds, label, used, remaining) -> None:
    usage = CodexUsage(
        account_id="account-1",
        plan_type="plus",
        rate_limit={
            "allowed": True,
            "limit_reached": False,
            "primary_window": {"used_percent": used, "limit_window_seconds": seconds, "reset_at": 1900000000},
        },
    )
    text = usage_text(usage, now=datetime.fromtimestamp(1900000000, UTC))
    assert f"{label} limit: {remaining}% remaining" in text
    assert "on " not in text.split("resets ")[1]
    assert "on " in usage_text(usage, now=datetime.fromtimestamp(1800000000, UTC)).split("resets ")[1]


async def test_missing_windows_are_not_zero_remaining() -> None:
    text = usage_text(
        CodexUsage(account_id="a", plan_type="plus", rate_limit={"allowed": True, "limit_reached": False})
    )
    assert "Usage windows unavailable" in text
    assert "% remaining" not in text


async def test_official_token_error_body_is_not_exposed_by_account_operations() -> None:
    requests = []

    def handle(request):
        requests.append(request.url.path)
        if request.url.path == "/oauth/token":
            return httpx2.Response(400, json={"error": "invalid_grant", "error_description": "private-token"})
        return httpx2.Response(401)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="could not be refreshed or saved") as caught:
            await CodexUsageClient(_source(), client=client).read()
    assert "private-token" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert requests == ["/backend-api/wham/usage", "/oauth/token"]
