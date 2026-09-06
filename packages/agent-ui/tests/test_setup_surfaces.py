from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from a13n_ui.app import open_agent_ui_app
from a13n_ui.model_accounts import (
    AccountProjection,
    Availability,
    CodexAccountStore,
    ExpiryStatus,
    GrokAccountStore,
    Provider,
    StoreKind,
)
from a13n_ui.settings import AgentUiSettings, StorageSettings
from a13n_ui.tui.application import AgentUiTerminalApp
from a13n_ui.tui.models import TerminalLifecycle
from a13n_ui.tui.screens.setup import SetupScreen
from textual.widgets import Button, Checkbox, Select, Static


@pytest.mark.anyio
async def test_first_use_terminal_setup_publishes_then_enters_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "codex").mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    async def codex_available(self):
        return AccountProjection(
            provider=Provider.CODEX,
            availability=Availability.AVAILABLE,
            source=StoreKind.FILE,
            usable=True,
            expiry=ExpiryStatus.VALID,
        )

    async def grok_absent(self):
        return AccountProjection(
            provider=Provider.GROK,
            availability=Availability.ABSENT,
            source=StoreKind.FILE,
            usable=False,
            expiry=ExpiryStatus.UNKNOWN,
        )

    monkeypatch.setattr(CodexAccountStore, "inspect", codex_available)
    monkeypatch.setattr(GrokAccountStore, "inspect", grok_absent)
    path = tmp_path / "config" / "config.yaml"

    @asynccontextmanager
    async def factory():
        async with open_agent_ui_app(
            AgentUiSettings(storage=StorageSettings(data_root=tmp_path / "state")), configuration_path=path
        ) as application:
            yield application

    terminal = AgentUiTerminalApp(app_factory=factory, launch_directory=tmp_path)
    async with terminal.run_test(size=(100, 48)) as pilot:
        async with asyncio.timeout(5):
            while not isinstance(terminal.screen, SetupScreen):
                await pilot.pause(0.02)
        await pilot.pause()
        screen = terminal.screen
        assert screen.query_one("#setup-codex", Checkbox).value, str(
            screen.query_one("#setup-message", Static).render()
        )
        assert screen.query_one("#setup-review", Checkbox).value
        assert screen.query_one("#setup-agent", Select).value == "agent-codex"
        assert not path.exists()
        assert screen._step == 1
        screen.query_one("#setup-next", Button).press()
        await pilot.pause()
        assert screen._step == 2
        screen.query_one("#setup-next", Button).press()
        await pilot.pause()
        assert screen._step == 3
        screen.query_one("#setup-preview", Button).press()
        async with asyncio.timeout(5):
            while screen.query_one("#setup-apply", Button).disabled:
                await pilot.pause(0.02)
        assert "Review" in str(screen.query_one("#setup-message", Static).render())
        screen.query_one("#setup-apply", Button).press()
        async with asyncio.timeout(5):
            while terminal.terminal_state.lifecycle is not TerminalLifecycle.READY:
                await pilot.pause(0.02)
        assert path.exists()
        assert "gpt-5.6-luna" in (path.parent / "models" / "codex-review.yaml").read_text()
        assert terminal.terminal_state.focused_thread_id is None
    await terminal.controller.close()


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["cancel", "native", "retry"])
async def test_conversation_sandbox_selector_recovers_without_silent_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    from a13n_ui.app import AgentUiApp
    from a13n_ui.setup import EnvironmentReadiness
    from a13n_ui.tui.intents import OpenOverlay, SelectConfigurationResource
    from a13n_ui.tui.screens.readiness import ReadinessScreen

    from .test_app import _write_configuration

    path = _write_configuration(tmp_path)
    calls = 0

    async def preflight(self: AgentUiApp, *, profile_id: str, project_path: str) -> EnvironmentReadiness:
        nonlocal calls
        calls += 1
        return EnvironmentReadiness(
            profile_id="environment-sandbox",
            ready=calls > 1,
            code="test",
            message="Isolation denied" if calls == 1 else "Ready",
        )

    monkeypatch.setattr(AgentUiApp, "preflight_environment", preflight)

    @asynccontextmanager
    async def factory():
        async with open_agent_ui_app(
            AgentUiSettings(storage=StorageSettings(data_root=tmp_path / "state")), configuration_path=path
        ) as application:
            yield application

    terminal = AgentUiTerminalApp(app_factory=factory, launch_directory=tmp_path / "workspace")
    async with terminal.run_test(size=(100, 40)) as pilot:
        async with asyncio.timeout(5):
            while terminal.terminal_state.lifecycle is not TerminalLifecycle.READY:
                await pilot.pause(0.02)
        await terminal.controller.handle(OpenOverlay("configuration", key="environment"))
        original = terminal.controller.state.draft_defaults.environment_profile_id
        terminal._submit_intent(SelectConfigurationResource(kind="environment", resource_id="environment-sandbox"))
        async with asyncio.timeout(5):
            while not isinstance(terminal.screen, ReadinessScreen):
                await pilot.pause(0.02)
        screen = terminal.screen
        async with asyncio.timeout(5):
            while screen.query_one("#readiness-retry", Button).disabled:
                await pilot.pause(0.02)
        assert terminal.controller.state.draft_defaults.environment_profile_id == original
        assert not terminal._intent_lock.locked()
        screen.query_one(f"#readiness-{action}", Button).press()
        async with asyncio.timeout(5):
            while terminal._preflight_open:
                await pilot.pause(0.02)
        await pilot.pause()
        expected = (
            original if action == "cancel" else "environment-native" if action == "native" else "environment-sandbox"
        )
        assert terminal.controller.state.draft_defaults.environment_profile_id == expected
        assert terminal.controller.state.focused_thread_id is None
        assert calls == (2 if action == "retry" else 1)
    await terminal.controller.close()


@pytest.mark.anyio
async def test_readiness_escape_cancels_inflight_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from a13n_ui.app import AgentUiApp
    from a13n_ui.tui.intents import SelectConfigurationResource
    from a13n_ui.tui.screens.readiness import ReadinessScreen

    from .test_app import _write_configuration

    path = _write_configuration(tmp_path)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def preflight(self: AgentUiApp, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(AgentUiApp, "preflight_environment", preflight)

    @asynccontextmanager
    async def factory():
        async with open_agent_ui_app(
            AgentUiSettings(storage=StorageSettings(data_root=tmp_path / "state")), configuration_path=path
        ) as application:
            yield application

    terminal = AgentUiTerminalApp(app_factory=factory, launch_directory=tmp_path / "workspace")
    async with terminal.run_test(size=(100, 40)) as pilot:
        async with asyncio.timeout(5):
            while terminal.terminal_state.lifecycle is not TerminalLifecycle.READY:
                await pilot.pause(0.02)
        terminal._submit_intent(SelectConfigurationResource(kind="environment", resource_id="environment-sandbox"))
        async with asyncio.timeout(5):
            await entered.wait()
        assert isinstance(terminal.screen, ReadinessScreen)
        await pilot.press("escape")
        async with asyncio.timeout(5):
            await cancelled.wait()
        assert terminal.controller.state.draft_defaults.environment_profile_id != "environment-sandbox"
    await terminal.controller.close()


@pytest.mark.anyio
async def test_terminal_not_now_checks_environment_before_agent_and_finishes_without_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a13n_ui.app import AgentUiApp
    from a13n_ui.setup import EnvironmentReadiness
    from textual.widgets import TextArea

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = tmp_path / "config" / "config.yaml"
    checked = []

    async def failed(self, profile_id, *, project_path):
        checked.append(project_path)
        return EnvironmentReadiness(
            profile_id=profile_id,
            ready=False,
            code="unavailable",
            message="Sandbox unavailable; choose Full Control or retry.",
        )

    monkeypatch.setattr(AgentUiApp, "preflight_environment", failed)

    @asynccontextmanager
    async def factory():
        async with open_agent_ui_app(
            AgentUiSettings(storage=StorageSettings(data_root=tmp_path / "state")), configuration_path=path
        ) as application:
            yield application

    terminal = AgentUiTerminalApp(app_factory=factory, launch_directory=tmp_path)
    async with terminal.run_test(size=(100, 48)) as pilot:
        async with asyncio.timeout(5):
            while not isinstance(terminal.screen, SetupScreen):
                await pilot.pause(0.02)
        screen = terminal.screen
        await pilot.pause()
        screen.query_one("#setup-skip", Button).press()
        await pilot.pause()
        assert screen._step == 2
        screen.query_one("#setup-environment", Select).value = "environment-sandbox"
        await pilot.pause()
        assert screen.query_one("#setup-next", Button).disabled
        screen.query_one("#setup-probe", Button).press()
        async with asyncio.timeout(5):
            while not checked or screen._busy:
                await pilot.pause(0.02)
        assert screen._step == 2 and not path.exists()
        assert screen.query_one("#setup-next", Button).disabled
        screen.query_one("#setup-environment", Select).value = "environment-native"
        await pilot.pause()
        screen.query_one("#setup-next", Button).press()
        await pilot.pause()
        assert screen._step == 3
        assert screen.query_one("#setup-agent", Select).value == "agent-default"
        screen.query_one("#setup-instructions", TextArea).load_text("Use short answers.")
        await pilot.pause()
        screen.query_one("#setup-back", Button).press()
        await pilot.pause()
        screen.query_one("#setup-next", Button).press()
        await pilot.pause()
        assert screen.query_one("#setup-instructions", TextArea).text == "Use short answers."
        screen.query_one("#setup-preview", Button).press()
        async with asyncio.timeout(5):
            while screen.query_one("#setup-apply", Button).disabled:
                await pilot.pause(0.02)
        screen.query_one("#setup-apply", Button).press()
        async with asyncio.timeout(5):
            while terminal.terminal_state.lifecycle is not TerminalLifecycle.READY:
                await pilot.pause(0.02)
    await terminal.controller.close()
    assert "Use short answers." in (path.parent / "agents" / "default.yaml").read_text()
    assert not (path.parent / "models").exists()


@pytest.mark.anyio
async def test_terminal_saves_key_and_publishes_only_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from textual.widgets import Input, TextArea

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "codex").mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("GROK_AUTH_PATH", str(tmp_path / "grok.json"))
    monkeypatch.delenv("GROK_AUTH", raising=False)
    path = tmp_path / "config" / "config.yaml"

    @asynccontextmanager
    async def factory():
        async with open_agent_ui_app(
            AgentUiSettings(storage=StorageSettings(data_root=tmp_path / "state")), configuration_path=path
        ) as application:
            yield application

    terminal = AgentUiTerminalApp(app_factory=factory, launch_directory=tmp_path)
    async with terminal.run_test(size=(110, 65)) as pilot:
        async with asyncio.timeout(5):
            while not isinstance(terminal.screen, SetupScreen):
                await pilot.pause(0.02)
        screen = terminal.screen
        await pilot.pause()
        screen.query_one("#setup-connection", Select).value = "api_key"
        screen.query_one("#setup-api-route", Input).value = "openai:test"
        screen.query_one("#setup-key-secret", Input).value = "private-terminal-key"
        await pilot.pause()
        screen.query_one("#setup-key-save", Button).press()
        async with asyncio.timeout(5):
            while "key-primary" not in screen._saved_keys or screen._busy:
                await pilot.pause(0.02)
        assert screen.query_one("#setup-key-secret", Input).value == ""
        assert not path.exists()
        screen.query_one("#setup-next", Button).press()
        await pilot.pause()
        screen.query_one("#setup-next", Button).press()
        await pilot.pause()
        screen.query_one("#setup-preview", Button).press()
        async with asyncio.timeout(5):
            while screen._preview is None:
                await pilot.pause(0.02)
        text = screen.query_one("#setup-files", TextArea).text
        assert "credential_ref: key-primary" in text
        assert "private-terminal-key" not in text
        screen.query_one("#setup-apply", Button).press()
        async with asyncio.timeout(5):
            while terminal.terminal_state.lifecycle is not TerminalLifecycle.READY:
                await pilot.pause(0.02)
    await terminal.controller.close()
    assert "credential_ref: key-primary" in (path.parent / "models" / "api-key.yaml").read_text()
