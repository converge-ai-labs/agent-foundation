from __future__ import annotations

import base64
import json
import os
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.interactive.onboarding import SetupBack, SetupCancelled, run_setup
from a13n_harness_ui.interactive.setup import SetupWizard
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings


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
@pytest.mark.parametrize("fast", ["", "on", "off"])
async def test_setup_reuses_existing_codex_login_without_login_or_refresh(
    tmp_path: Path, expired: bool, fast: str
) -> None:
    account = _codex_login_file(datetime.now(UTC) + timedelta(hours=-1 if expired else 1))
    original = account.read_bytes()
    path = tmp_path / "config" / "a13n-harness-ui.yaml"
    answers = deque(["codex", "gpt-5.6-sol", fast, "full-control"])
    asked, output = [], []

    async def ask(question, selection):
        asked.append(question.key)
        return answers.popleft()

    async def unexpected(*args, **kwargs):
        pytest.fail("Setup discovery must not authenticate, refresh, or prepare a runtime")

    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")),
        configuration_path=path,
        codex_login=unexpected,
        codex_refresh=unexpected,
    ) as app:
        assert await run_setup(app, tmp_path, ask_user=ask, emit=output.append)
    assert asked == ["provider", "model", "fast", "environment"]
    assert not answers
    assert "Using existing Codex login" in "\n".join(output)
    assert account.read_bytes() == original
    assert "fixture-refresh" not in "\n".join(output)
    configuration = await load_harness_ui_configuration(path)
    model = configuration.models["model-codex"]
    assert model.settings["thinking"] == "high"
    assert model.settings["service_tier"] == ("default" if fast == "off" else "priority")
    assert model.model_characteristics.context_window == 350000
    assert configuration.document.defaults.environment_profile == "environment-native"


@pytest.mark.anyio
async def test_setup_reuses_an_existing_multi_root_project_without_publishing_a_duplicate(
    tmp_path: Path,
) -> None:
    _codex_login_file(datetime.now(UTC) + timedelta(hours=1))
    directory, notes = tmp_path / "code", tmp_path / "notes"
    directory.mkdir()
    notes.mkdir()
    path = tmp_path / "config" / "a13n-harness-ui.yaml"
    path.parent.mkdir()
    path.write_text('schema_version: "2"\n')
    project = path.parent / "projects" / "custom.yaml"
    project.parent.mkdir()
    project.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "kind": "project",
                "id": "project-custom",
                "name": "Code and notes",
                "roots": [{"path": str(directory)}, {"path": str(notes)}],
            }
        )
    )
    original = project.read_bytes()
    output = []
    answers = deque(["codex", "gpt-5.6-sol", "", "full-control"])

    async def ask(question, selection):
        return answers.popleft()

    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")),
        configuration_path=path,
    ) as app:
        assert await run_setup(app, directory, ask_user=ask, emit=output.append)
        configuration = await app.current_configuration()
        assert set(configuration.projects) == {"project-custom"}
        assert configuration.document.defaults.project == "project-custom"
        assert await app.ensure_cwd_project(directory) == "project-custom"
    assert project.read_bytes() == original
    assert not (project.parent / "project-local.yaml").exists()


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
    answers = deque(["grok", "grok-4.6", "full-control"])
    output = []

    async def ask(question, selection):
        if question.key == "provider":
            assert question.default == "grok"
            assert "existing login" in selection.choices[1].description
        return answers.popleft()

    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")),
        configuration_path=tmp_path / "config.yaml",
    ) as app:
        assert await run_setup(app, tmp_path, ask_user=ask, emit=output.append)
    assert "Using existing Grok login" in "\n".join(output)
    assert grok.read_bytes() == original
    assert "fixture-access" not in "\n".join(output)


@pytest.mark.anyio
@pytest.mark.parametrize("cancel_at", ["provider", "model", "fast", "environment"])
async def test_cancel_setup_does_not_publish_configuration(tmp_path: Path, cancel_at: str) -> None:
    path = tmp_path / "config" / "a13n-harness-ui.yaml"
    account = _codex_login_file(datetime.now(UTC) + timedelta(hours=1))
    original = account.read_bytes()

    async def ask(question, selection):
        if question.key == cancel_at:
            raise SetupCancelled()
        return question.default

    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")),
        configuration_path=path,
    ) as app:
        assert not await run_setup(app, tmp_path, ask_user=ask, emit=lambda text: None)
    assert not path.exists()
    assert not (path.parent / "models").exists()
    assert account.read_bytes() == original


@pytest.mark.anyio
async def test_missing_account_requires_external_login_and_allows_retry(tmp_path: Path) -> None:
    Path(os.environ["CODEX_HOME"]).mkdir(parents=True)
    answers = deque(["codex", "later", "gpt-5.6-sol", "", "full-control"])
    asked = []

    async def ask(question, selection):
        asked.append(question.text)
        if question.text == "Connect Codex":
            assert [choice.value for choice in selection.choices] == ["retry", "later"]
        return answers.popleft()

    async def unexpected(request):
        pytest.fail("Login requires an explicit selection")

    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")),
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
            SetupBack(),
            "api",
            "openai-responses",
            "https://api.openai.com/v1",
            "env:TEST_KEY",
            "gpt-test",
            "high",
            "",
            "full-control",
        ]
    )

    async def ask(question, selection):
        value = answers.popleft()
        if isinstance(value, Exception):
            raise value
        return value

    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")),
        configuration_path=tmp_path / "config.yaml",
    ) as app:
        assert await run_setup(app, tmp_path, ask_user=ask, emit=lambda text: None)
    assert not answers


def test_custom_settings_are_optional_and_connection_change_clears_stale_values() -> None:
    wizard = SetupWizard(advanced=True)
    wizard.accept("codex")
    wizard.accept("gpt-6-astra")
    for value in ("on", "all", "extended", "low", "no", "be concise", "full-control"):
        wizard.accept(value)
    assert wizard.question is None
    assert wizard.selection("/tmp")["codex_context_window"] == 872000
    while wizard.history:
        assert wizard.back()
    wizard.accept("api")
    assert wizard.values == {"provider": "api"}
    assert wizard.advanced


@pytest.mark.anyio
async def test_landing_reuses_one_application_and_replaces_question_content() -> None:
    import asyncio

    from a13n_harness_ui.interactive.onboarding import LandingScreen
    from a13n_harness_ui.interactive.setup import Question
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
            from a13n_harness_ui.interactive.onboarding import SetupBack

            with pytest.raises(SetupBack):
                await asyncio.wait_for(second, 2)
        assert not application.is_running


@pytest.mark.anyio
async def test_landing_choices_use_available_rows_and_reflow_on_resize(monkeypatch) -> None:
    from a13n_harness_ui.interactive.onboarding import LandingScreen
    from a13n_harness_ui.interactive.selection import Choice, Selection
    from a13n_harness_ui.interactive.setup import Question
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.utils import get_cwidth

    output = DummyOutput()
    size = Size(rows=40, columns=120)
    monkeypatch.setattr(output, "get_size", lambda: size)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
        screen = LandingScreen()
        screen.notice = "Choose a provider"
        screen.question = Question("provider", "Available providers", "0")
        screen.selection = Selection(
            tuple(Choice(str(i), f"Provider {i}", "Wide description " * 2) for i in range(30)), cursor=0
        )

        def visible():
            return "".join(text for _, text in screen._choices())

        assert visible().count("Provider ") == 30  # Not capped at four or eight.
        size = Size(rows=15, columns=120)
        assert visible().count("Provider ") == 7
        screen.selection.move(29)
        assert "Provider 29" in visible()
        size = Size(rows=22, columns=35)
        screen.notice = "A long wrapped notice " * 3
        text = visible()
        rows = sum(max(1, (get_cwidth(line) + size.columns - 1) // size.columns) for line in text.splitlines())
        header_rows = (get_cwidth("  " + screen.notice) + size.columns - 1) // size.columns + 1
        assert rows <= size.rows - 6 - header_rows
        assert "Provider 29" in text
        assert text.count("Provider ") < 7


@pytest.mark.anyio
async def test_landing_chat_keeps_terminal_and_enables_composer_only_when_ready() -> None:
    import asyncio
    from types import SimpleNamespace

    from a13n_harness_ui.cli import CliRequest
    from a13n_harness_ui.interactive.onboarding import LandingScreen
    from a13n_harness_ui.interactive.shell import CliShell
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    preparing, release = asyncio.Event(), asyncio.Event()

    async def skill_catalog():
        preparing.set()
        await release.wait()
        return None

    async def empty():
        return None

    backend = SimpleNamespace(
        thread_id=None, resumed_transcript=None, interaction=empty, skill_catalog=skill_catalog, cancel=empty
    )
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        async with LandingScreen() as screen:
            application = screen.application
            shell = CliShell(CliRequest())
            chat = asyncio.create_task(screen.run_chat(shell, backend))
            await asyncio.wait_for(preparing.wait(), 2)
            assert shell.app is application and application.is_running
            assert not shell.ready
            assert application.renderer.mouse_support()
            shell.mouse = False
            assert not application.renderer.mouse_support()
            pipe.send_text("draft\r")
            await asyncio.sleep(0.1)
            assert shell.composer.text == "draft"
            release.set()
            async with asyncio.timeout(2):
                while not shell.ready:
                    await asyncio.sleep(0.01)
            shell.composer.text = ""
            pipe.send_text("/quit\r")
            await asyncio.wait_for(chat, 2)
        assert not application.is_running
    assert loop.get_exception_handler() is previous_handler


@pytest.mark.anyio
async def test_landing_busy_cancellation_releases_terminal() -> None:
    import asyncio

    from a13n_harness_ui.interactive.onboarding import LandingScreen
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


@pytest.mark.anyio
async def test_add_agent_wizard_has_no_publication_confirmation_and_keeps_defaults(tmp_path: Path) -> None:
    _codex_login_file(datetime.now(UTC) + timedelta(hours=1))
    path = tmp_path / "config.yaml"
    answers = deque(["codex", "gpt-5.6-sol", "", "full-control"])
    asked = []

    async def ask(question, selection):
        asked.append(question.key)
        return answers.popleft()

    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")),
        configuration_path=path,
    ) as app:
        assert await run_setup(app, tmp_path, ask_user=ask, emit=lambda text: None)
        root = path.read_bytes()
        asked.clear()
        answers.extend(["new", "codex", "gpt-6-astra", "", "Astra coding"])
        assert await run_setup(app, tmp_path, ask_user=ask, emit=lambda text: None, add_agent=True)
        assert asked == ["model_source", "provider", "model", "fast", "name"]
        assert path.read_bytes() == root
        source = await app.current_configuration()
        assert source.models[source.agents["agent-astra-coding"].model].route == "openai-codex:gpt-6-astra"
        assert source.document.defaults.agent == "agent-codex"


@pytest.mark.anyio
@pytest.mark.parametrize("api", [False, True])
async def test_add_model_only_then_add_agent_reuses_exact_model(tmp_path: Path, api: bool) -> None:
    _codex_login_file(datetime.now(UTC) + timedelta(hours=1))
    path = tmp_path / "config.yaml"
    answers = deque(["codex", "gpt-5.6-sol", "", "full-control"])
    asked = []

    async def ask(question, selection):
        asked.append(question.key)
        assert answers, question
        return answers.popleft()

    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")),
        configuration_path=path,
    ) as app:
        assert await run_setup(app, tmp_path, ask_user=ask, emit=lambda text: None)
        baseline = {p: p.read_bytes() for p in tmp_path.rglob("*.yaml")}
        for _ in range(2):
            asked.clear()
            answers.extend(
                [
                    "api",
                    "anthropic",
                    "https://api.anthropic.com",
                    "env:TEST_KEY",
                    "claude-sonnet-4-6",
                    "adaptive",
                    "",
                    "Shared model",
                ]
                if api
                else ["codex", "gpt-6-astra", "", "Shared model"]
            )
            assert await run_setup(app, tmp_path, ask_user=ask, emit=lambda text: None, add_model=True)
            assert asked == (
                ["provider", "api_provider", "base_url", "credential", "model", "preset", "context", "name"]
                if api
                else ["provider", "model", "fast", "name"]
            )
        source = await app.current_configuration()
        assert set(source.agents) == {"agent-codex"}
        assert {"model-shared-model", "model-shared-model-2"} <= source.models.keys()
        assert all(p.read_bytes() == content for p, content in baseline.items())
        models = {p: p.read_bytes() for p in (tmp_path / "models").glob("*.yaml")}
        for name in ("First agent", "Second agent"):
            asked.clear()
            answers.extend(["model-shared-model", name])
            assert await run_setup(app, tmp_path, ask_user=ask, emit=lambda text: None, add_agent=True)
            assert asked == ["model_source", "name"]
        source = await app.current_configuration()
        assert source.agents["agent-first-agent"].model == "model-shared-model"
        assert source.agents["agent-second-agent"].model == "model-shared-model"
        review = next(
            (c for c in source.agents["agent-first-agent"].capabilities if c.capability == "ShellReviewCapability"),
            None,
        )
        assert (review is None) is api
        if review is not None:
            assert review.configuration["risk_threshold"] == "extra_high"
            assert review.configuration["on_flagged"] == "approval_required"
            assert review.configuration["on_error"] == "skip"
        assert set((tmp_path / "models").glob("*.yaml")) == set(models)
        assert all(p.read_bytes() == content for p, content in {**baseline, **models}.items())


@pytest.mark.anyio
async def test_add_agent_can_back_out_of_existing_model_and_create_new(tmp_path: Path) -> None:
    _codex_login_file(datetime.now(UTC) + timedelta(hours=1))
    answers = deque(["codex", "gpt-5.6-sol", "", "full-control"])

    async def ask(question, selection):
        value = answers.popleft()
        if isinstance(value, Exception):
            raise value
        return value

    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")),
        configuration_path=tmp_path / "config.yaml",
    ) as app:
        assert await run_setup(app, tmp_path, ask_user=ask, emit=lambda text: None)
        answers.extend(["model-codex", SetupBack(), "new", "codex", "gpt-6-astra", "", "Independent"])
        assert await run_setup(app, tmp_path, ask_user=ask, emit=lambda text: None, add_agent=True)
        source = await app.current_configuration()
        assert source.agents["agent-independent"].model == "model-agent-independent"
        assert source.models["model-agent-independent"].route == "openai-codex:gpt-6-astra"
