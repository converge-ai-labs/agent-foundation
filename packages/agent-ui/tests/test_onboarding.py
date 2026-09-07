from __future__ import annotations

import base64
import json
import os
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from a13n_ui.app import open_agent_ui_app
from a13n_ui.configuration import load_agent_ui_configuration
from a13n_ui.interactive.onboarding import SetupBack, SetupCancelled, run_setup
from a13n_ui.interactive.setup import SetupWizard
from a13n_ui.settings import AgentUiSettings, StorageSettings


def _codex_login_file(expiry: datetime) -> Path:
    home = Path(os.environ["CODEX_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    claims = base64.urlsafe_b64encode(json.dumps({"exp": int(expiry.timestamp())}).encode()).decode().rstrip("=")
    path = home / "auth.json"
    path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": f"e30.{claims}.fixture",
                    "id_token": f"e30.{claims}.fixture",
                    "refresh_token": "fixture-refresh",
                    "account_id": "fixture-account",
                },
            }
        )
    )
    return path


@pytest.mark.anyio
@pytest.mark.parametrize("expired", [False, True])
async def test_setup_reuses_existing_codex_login_without_login_or_refresh(tmp_path: Path, expired: bool) -> None:
    account = _codex_login_file(datetime.now(UTC) + timedelta(hours=-1 if expired else 1))
    original = account.read_bytes()
    path = tmp_path / "config" / "a13n-ui.yaml"
    answers = deque(["codex", "full-control", "save"])
    asked, output = [], []

    async def ask(question, selection):
        asked.append(question.key)
        return answers.popleft()

    async def unexpected(*args, **kwargs):
        pytest.fail("Setup discovery must not authenticate, refresh, or prepare a runtime")

    async with open_agent_ui_app(
        AgentUiSettings(storage=StorageSettings(data_root=tmp_path / "data")),
        configuration_path=path,
        codex_login=unexpected,
        codex_refresh=unexpected,
    ) as app:
        assert await run_setup(app, tmp_path, ask_user=ask, emit=output.append)
    assert asked == ["provider", "environment", "action"]
    assert not answers
    assert "Using existing Codex login" in "\n".join(output)
    assert account.read_bytes() == original
    assert "fixture-refresh" not in "\n".join(output)
    configuration = await load_agent_ui_configuration(path)
    model = configuration.models["model-codex"]
    assert model.settings["thinking"] == "high"
    assert model.model_characteristics.context_window == 350000
    assert configuration.document.defaults.environment_profile == "environment-native"


@pytest.mark.anyio
async def test_setup_available_grok_is_default_even_with_broken_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = Path(os.environ["CODEX_HOME"])
    codex.mkdir(parents=True)
    (codex / "auth.json").write_text("broken json")
    grok = tmp_path / "grok-auth.json"
    monkeypatch.setenv("GROK_AUTH_PATH", str(grok))
    grok.write_text(
        json.dumps(
            {
                "https://issuer.example::client": {
                    "key": "fixture-access",
                    "auth_mode": "oidc",
                    "create_time": datetime.now(UTC).isoformat(),
                    "user_id": "fixture-user",
                    "refresh_token": "fixture-refresh",
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "oidc_issuer": "https://issuer.example",
                    "oidc_client_id": "client",
                }
            }
        )
    )
    original = grok.read_bytes()
    answers = deque(["grok", "full-control", "save"])
    output = []

    async def ask(question, selection):
        if question.key == "provider":
            assert question.default == "grok"
            assert "existing login" in selection.choices[1].description
        return answers.popleft()

    async with open_agent_ui_app(
        AgentUiSettings(storage=StorageSettings(data_root=tmp_path / "data")),
        configuration_path=tmp_path / "config.yaml",
    ) as app:
        assert await run_setup(app, tmp_path, ask_user=ask, emit=output.append)
    assert "Using existing Grok login" in "\n".join(output)
    assert grok.read_bytes() == original
    assert "fixture-access" not in "\n".join(output)


@pytest.mark.anyio
@pytest.mark.parametrize("cancel_at", ["provider", "action"])
async def test_cancel_setup_does_not_publish_configuration(tmp_path: Path, cancel_at: str) -> None:
    path = tmp_path / "config" / "a13n-ui.yaml"
    account = _codex_login_file(datetime.now(UTC) + timedelta(hours=1))
    original = account.read_bytes()

    async def ask(question, selection):
        if question.key == cancel_at:
            raise SetupCancelled()
        return question.default

    async with open_agent_ui_app(
        AgentUiSettings(storage=StorageSettings(data_root=tmp_path / "data")),
        configuration_path=path,
    ) as app:
        assert not await run_setup(app, tmp_path, ask_user=ask, emit=lambda text: None)
    assert not path.exists()
    assert not (path.parent / "models").exists()
    assert account.read_bytes() == original


@pytest.mark.anyio
async def test_missing_account_offers_explicit_login_but_does_not_start_it(tmp_path: Path) -> None:
    Path(os.environ["CODEX_HOME"]).mkdir(parents=True)
    answers = deque(["codex", "full-control", "later", "save"])
    asked = []

    async def ask(question, selection):
        asked.append(question.text)
        if question.text == "Connect Codex":
            assert "login" in [choice.value for choice in selection.choices]
        return answers.popleft()

    async def unexpected(request):
        pytest.fail("Login requires an explicit selection")

    async with open_agent_ui_app(
        AgentUiSettings(storage=StorageSettings(data_root=tmp_path / "data")),
        configuration_path=tmp_path / "config.yaml",
        codex_login=unexpected,
    ) as app:
        assert await run_setup(app, tmp_path, ask_user=ask, emit=lambda text: None)
    assert "Connect Codex" in asked


@pytest.mark.anyio
async def test_back_from_login_can_choose_another_connection(tmp_path: Path) -> None:
    answers = deque(
        [
            "codex",
            "full-control",
            SetupBack(),
            SetupBack(),
            "api",
            "openai:gpt-test",
            "env:TEST_KEY",
            "full-control",
            "save",
        ]
    )

    async def ask(question, selection):
        value = answers.popleft()
        if isinstance(value, Exception):
            raise value
        return value

    async with open_agent_ui_app(
        AgentUiSettings(storage=StorageSettings(data_root=tmp_path / "data")),
        configuration_path=tmp_path / "config.yaml",
    ) as app:
        assert await run_setup(app, tmp_path, ask_user=ask, emit=lambda text: None)
    assert not answers


def test_custom_settings_are_optional_and_connection_change_clears_stale_values() -> None:
    wizard = SetupWizard()
    wizard.accept("codex")
    wizard.accept("full-control")
    assert wizard.question is None
    wizard.customize()
    for value in ("gpt-6-astra", "extended", "low", "no", "be concise"):
        wizard.accept(value)
    assert wizard.question is None
    assert wizard.selection("/tmp")["codex_context_window"] == 872000
    while wizard.index:
        assert wizard.back()
    wizard.accept("api")
    assert wizard.values == {"provider": "api"}
    assert not wizard.advanced


@pytest.mark.anyio
async def test_landing_reuses_one_application_and_replaces_question_content() -> None:
    import asyncio

    from a13n_ui.interactive.onboarding import LandingScreen
    from a13n_ui.interactive.setup import Question
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        async with LandingScreen() as screen:
            application = screen.application
            assert application.full_screen
            screen.emit("First step status")
            first = asyncio.create_task(screen.ask(Question("first", "First question", "first value", ())))
            await asyncio.sleep(0.02)
            pipe.send_text("answer one\r")
            assert await asyncio.wait_for(first, 2) == "answer one"
            screen.emit("Second step status")
            second = asyncio.create_task(screen.ask(Question("second", "Second question", "second value", ())))
            await asyncio.sleep(0.02)
            assert screen.application is application
            assert screen.notice == "Second step status"
            assert screen.question.text == "Second question"
            assert screen.field.text == ""
            assert "first value" not in str(screen._choices())
            pipe.send_text("\x1b")
            from a13n_ui.interactive.onboarding import SetupBack

            with pytest.raises(SetupBack):
                await asyncio.wait_for(second, 2)
        assert not application.is_running


@pytest.mark.anyio
async def test_landing_busy_cancellation_releases_terminal() -> None:
    import asyncio

    from a13n_ui.interactive.onboarding import LandingScreen
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    ready = asyncio.Event()

    async def prepare():
        async with screen:
            ready.set()
            await asyncio.sleep(60)

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        screen = LandingScreen()
        task = asyncio.create_task(prepare())
        await ready.wait()
        pipe.send_text("\x03")
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    assert screen.cancel_requested
    assert not screen.application.is_running
