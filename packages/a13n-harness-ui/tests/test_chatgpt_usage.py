"""SIWC status uses account inspection, never Codex quota or credential refresh."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.shell import CliShell
from a13n_harness_ui.model_accounts.models import (
    AccountProjection,
    Availability,
    ExpiryStatus,
    Provider,
    StoreKind,
)
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("command", ["/status", "/usage subscription"])
@pytest.mark.parametrize("connected", [False, True])
async def test_chatgpt_subscription_status_is_nonmodal_and_does_not_read_codex(command, connected):
    account = AccountProjection(
        provider=Provider.CHATGPT,
        availability=Availability.AVAILABLE if connected else Availability.ABSENT,
        source=StoreKind.FILE,
        usable=connected,
        expiry=ExpiryStatus.EXPIRED if connected else ExpiryStatus.UNKNOWN,
        account_id="chatgpt-subject" if connected else None,
    )
    app = SimpleNamespace(
        inspect_model_account=AsyncMock(return_value=account),
        codex_usage=AsyncMock(),
        redeem_codex_reset=AsyncMock(),
    )
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = SimpleNamespace(app=app)
        shell.status.model = "openai-chatgpt:fixture"
        shell.composer.text = "keep this draft"
        await shell.command(shell.registry.parse(command))
        await shell.job
        assert shell.menu_handler is None and shell.selection is None
        assert shell.composer.text == "keep this draft"
        app.inspect_model_account.assert_awaited_once_with(Provider.CHATGPT)
        app.codex_usage.assert_not_awaited()
        app.redeem_codex_reset.assert_not_awaited()
        text = "".join(block.source for block in shell.renderer.transcript.blocks.values())
        assert "ChatGPT subscription" in text
        assert "Remaining allowance and reset times are unavailable" in text
        assert "https://chatgpt.com/settings/usage" in text
        assert "same ChatGPT account and workspace" in text
        assert "% remaining" not in text and "Reset credits:" not in text
        assert ("Selected account: chatgpt-subject" in text) is connected
        assert ("a13n-harness-ui login chatgpt" in text) is not connected


async def test_chatgpt_cannot_redeem_codex_reset_credits():
    app = SimpleNamespace(inspect_model_account=AsyncMock(), codex_usage=AsyncMock(), redeem_codex_reset=AsyncMock())
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = SimpleNamespace(app=app)
        shell.status.model = "openai-chatgpt:fixture"
        with pytest.raises(ValueError, match="Reset credits are available for Codex Models only"):
            await shell.command(shell.registry.parse("/usage reset"))
        app.inspect_model_account.assert_not_awaited()
        app.codex_usage.assert_not_awaited()
        app.redeem_codex_reset.assert_not_awaited()


async def test_chatgpt_usage_inspects_real_host_store_without_refresh(tmp_path, monkeypatch):
    from a13n_harness_ui.interactive.usage import show_subscription_usage

    from .test_chatgpt_accounts import signed_in

    store = await signed_in(tmp_path / "auth.json", monkeypatch)
    monkeypatch.setattr(store, "load", AsyncMock(side_effect=AssertionError("status must not refresh credentials")))

    async def inspect(provider):
        assert provider is Provider.CHATGPT
        return await store.inspect()

    app = SimpleNamespace(inspect_model_account=inspect)
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.backend = SimpleNamespace(app=app)
        shell.status.model = "openai-chatgpt:fixture"
        await show_subscription_usage(shell)
        text = "".join(block.source for block in shell.renderer.transcript.blocks.values())
        assert "Selected account: test-subject" in text
        assert "private-" not in text
        store.load.assert_not_awaited()
