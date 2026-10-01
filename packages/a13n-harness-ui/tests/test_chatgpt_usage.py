"""SIWC usage shows only the official URL, without querying either account API."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.shell import CliShell
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("command", ["/status", "/usage subscription"])
async def test_chatgpt_subscription_status_is_nonmodal_and_does_not_query_accounts(command):
    app = SimpleNamespace(
        inspect_model_account=AsyncMock(),
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
        app.inspect_model_account.assert_not_awaited()
        app.codex_usage.assert_not_awaited()
        app.redeem_codex_reset.assert_not_awaited()
        text = "".join(block.source for block in shell.renderer.transcript.blocks.values())
        assert "ChatGPT subscription" in text
        assert "ChatGPT subscription usage is unavailable in Harness UI." in text
        assert "Manage usage in ChatGPT: https://chatgpt.com/settings/usage" in text
        assert "% remaining" not in text and "Reset credits:" not in text
        assert "Selected account:" not in text and "login chatgpt" not in text


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
