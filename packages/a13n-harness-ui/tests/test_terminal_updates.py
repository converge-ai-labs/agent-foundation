"""Update detection, consent, and terminal ownership across startup boundaries."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from a13n_harness_ui import terminal, updater
from a13n_harness_ui.cli import CliRequest, cli
from a13n_harness_ui.configuration import empty_harness_ui_configuration
from a13n_harness_ui.interactive import onboarding, shell, startup, updates
from click.testing import CliRunner


@pytest.mark.parametrize("arguments", [["--no-update-check"], ["--no-update-check", "setup"]])
def test_cli_can_disable_update_check_for_one_invocation(monkeypatch: pytest.MonkeyPatch, arguments: list[str]) -> None:
    requests = []
    monkeypatch.setattr(terminal, "start", requests.append)
    result = CliRunner().invoke(cli, arguments)
    assert result.exit_code == 0, result.output
    assert requests[0].no_update_check


def test_update_command_requires_the_running_uv_tool_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = tmp_path / "tools" / "a13n-harness-ui"
    prefix.mkdir(parents=True)
    monkeypatch.setattr(updater.sys, "prefix", str(prefix))
    monkeypatch.setattr(updater.shutil, "which", lambda name: "/bin/uv")
    assert updates.update_command() is None
    (prefix / "uv-receipt.toml").touch()
    command = updates.update_command()
    assert command == updates.UpdateCommand("/bin/uv", prefix.parent)
    assert command.argv == ("/bin/uv", "tool", "upgrade", "a13n-harness-ui")
    monkeypatch.setattr(updater.shutil, "which", lambda name: None)
    assert updates.update_command() is None


@pytest.mark.anyio
@pytest.mark.parametrize("answer", ["update", "later", "1", "2", onboarding.SetupBack, onboarding.SetupCancelled])
async def test_update_prompt_requires_fresh_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str | type[Exception]
) -> None:
    asked, notices = [], []
    command = updates.UpdateCommand("uv", tmp_path)

    async def check(root):
        return updates.AvailableUpdate("1.0", "2.0")

    async def ask(question, selection):
        asked.append(question)
        assert question.default == updates.resolve_choice(selection.answer(), question.choices) == "later"
        if isinstance(answer, str):
            return answer
        raise answer()

    monkeypatch.setattr(updates, "check_update", check)
    monkeypatch.setattr(updates, "update_command", lambda: command)
    landing = SimpleNamespace(title="", emit=notices.append, ask=ask)
    for _ in range(2):
        result = await updates.prompt_update(tmp_path, landing)
        assert result == (command if answer in ("update", "1") else None)
    assert len(asked) == 2
    assert "uv tool upgrade a13n-harness-ui" in "\n".join(notices)
    assert notices[-1] == "Checking local configuration…"


@pytest.mark.anyio
async def test_invalid_update_choices_redraw_and_retry_without_installing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notices = []
    answers = iter(["3", "1,2", "garbage", "later"])
    asked = []

    async def check(root):
        return updates.AvailableUpdate("1.0", "2.0")

    async def ask(question, selection):
        asked.append(question)
        return next(answers)

    monkeypatch.setattr(updates, "check_update", check)
    monkeypatch.setattr(updates, "update_command", lambda: updates.UpdateCommand("uv", tmp_path))
    landing = SimpleNamespace(title="", emit=notices.append, ask=ask)
    assert await updates.prompt_update(tmp_path, landing) is None
    assert len(asked) == 4
    assert "Choose a number from 1 to 2" in notices[2]
    assert "This question accepts one option" in notices[3]
    assert "Choose one of the displayed options" in notices[4]
    assert all("Run: uv tool upgrade a13n-harness-ui" in notice for notice in notices[1:-1])


@pytest.mark.anyio
async def test_unknown_installer_gets_instructions_not_an_install_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notices = []

    async def check(root):
        return updates.AvailableUpdate("1.0", "2.0")

    async def ask(question, selection):
        assert [choice.value for choice in selection.choices] == ["later"]
        return "later"

    monkeypatch.setattr(updates, "check_update", check)
    monkeypatch.setattr(updates, "update_command", lambda: None)
    assert await updates.prompt_update(tmp_path, SimpleNamespace(title="", emit=notices.append, ask=ask)) is None
    assert "original package manager" in "\n".join(notices)


@pytest.mark.anyio
@pytest.mark.parametrize("disabled", ["none", "config", "flag"])
@pytest.mark.parametrize("install", [False, True])
async def test_update_precedes_setup_and_handoff_closes_app_and_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disabled: str, install: bool
) -> None:
    events = []
    command = updates.UpdateCommand("uv", tmp_path)
    configuration = empty_harness_ui_configuration()
    if disabled == "config":
        configuration = configuration.model_copy(
            update={
                "document": configuration.document.model_copy(
                    update={
                        "process": configuration.document.process.model_copy(update={"terminal_update_check": False})
                    }
                )
            }
        )

    class Landing:
        notice = ""
        cancel_requested = False

        async def __aenter__(self):
            events.append("landing-open")
            return self

        async def __aexit__(self, *args):
            events.append("landing-close")

        async def close(self):
            events.append("landing-release")

        async def run_chat(self, shell, backend):
            events.append("landing-chat")
            await shell.run(backend)

        def emit(self, text):
            self.notice = text

        async def ask(self, *args):
            pytest.fail("Setup questions are replaced by the setup boundary in this test")

    async def current_configuration():
        return configuration

    initialized = False

    async def initialize():
        nonlocal initialized
        events.append("initialize")
        previous, initialized = initialized, True
        return previous

    @asynccontextmanager
    async def factory(*args):
        events.append("app-open")
        try:
            yield SimpleNamespace(
                app=SimpleNamespace(current_configuration=current_configuration),
                environment=None,
                initialize=initialize,
            )
        finally:
            events.append("app-close")

    async def prompt(*args):
        events.append("update")
        return command if install else None

    async def setup(*args, **kwargs):
        events.append("setup")
        return True

    async def chat(*args):
        events.append("chat")

    monkeypatch.setattr(onboarding, "LandingScreen", Landing)
    monkeypatch.setattr(onboarding, "run_setup", setup)
    monkeypatch.setattr(updates, "prompt_update", prompt)
    # This checks startup orchestration, not native console construction (covered
    # by the PTY suite). Windows CI has no console even when run() is replaced.
    monkeypatch.setattr(shell, "CliShell", lambda *args, **kwargs: SimpleNamespace(run=chat))
    result = await startup.run_terminal(
        CliRequest(no_update_check=disabled == "flag"), directory=tmp_path, runtime_loader=lambda: factory
    )
    assert events[0] == "landing-open"
    assert events[-2:] == ["app-close", "landing-close"]
    if disabled != "none":
        assert "update" not in events
    elif install:
        assert result == command
        assert "initialize" not in events and "setup" not in events and "chat" not in events
        return
    else:
        assert events.index("update") < events.index("initialize") < events.index("setup")
    assert result is None
    assert "landing-release" not in events
    assert events.index("setup") < events.index("landing-chat") < events.index("chat")


@pytest.mark.parametrize("exit_code", [0, 7])
def test_installer_runs_only_after_async_terminal_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], exit_code: int
) -> None:
    events = []
    command = updates.UpdateCommand("uv", tmp_path)

    async def run(request):
        events.append("cleanup")
        return command

    def install(argv, **kwargs):
        assert events == ["cleanup"]
        events.append("install")
        assert argv == command.argv
        assert kwargs == {"env": {**updater.os.environ, "UV_TOOL_DIR": str(tmp_path)}, "check": False}
        return SimpleNamespace(returncode=exit_code)

    monkeypatch.setattr(terminal.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(terminal.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(startup, "run_terminal", run)
    monkeypatch.setattr(updater.subprocess, "run", install)
    if exit_code:
        with pytest.raises(click.exceptions.Exit) as exc:
            terminal.start(CliRequest())
        assert exc.value.exit_code == exit_code
    else:
        terminal.start(CliRequest())
    assert events == ["cleanup", "install"]
    output = capsys.readouterr()
    assert ("Update complete" in output.out) == (exit_code == 0)
    assert ("Update failed" in output.err) == (exit_code != 0)


@pytest.mark.parametrize("arguments", [["update"], ["--no-update-check", "update"]])
@pytest.mark.parametrize("exit_code", [0, 7, -15])
def test_explicit_update_runs_without_terminal_setup_or_cached_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str], exit_code: int
) -> None:
    prefix = tmp_path / "tools" / "a13n-harness-ui"
    prefix.mkdir(parents=True)
    (prefix / "uv-receipt.toml").touch()
    monkeypatch.setattr(updater.sys, "prefix", str(prefix))
    monkeypatch.setattr(updater.shutil, "which", lambda name: "/bin/uv")
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "other-tools"))

    def unexpected(*args, **kwargs):
        pytest.fail("Explicit updates must not enter chat/setup or use startup detection")

    monkeypatch.setattr(terminal, "start", unexpected)
    monkeypatch.setattr(updates, "check_update", unexpected)
    calls = []

    def install(argv, **kwargs):
        calls.append(argv)
        assert argv == ("/bin/uv", "tool", "upgrade", "a13n-harness-ui")
        assert kwargs == {"env": {**updater.os.environ, "UV_TOOL_DIR": str(prefix.parent)}, "check": False}
        return SimpleNamespace(returncode=exit_code)

    monkeypatch.setattr(updater.subprocess, "run", install)
    result = CliRunner().invoke(cli, arguments)
    assert result.exit_code == (exit_code if exit_code >= 0 else 1)
    assert len(calls) == 1
    assert f"Tool directory: {prefix.parent}" in result.output
    assert ("Update complete" in result.output) == (exit_code == 0)
    assert ("Update failed" in result.output) == (exit_code != 0)


@pytest.mark.parametrize("recognized", [False, True])
def test_explicit_update_does_not_guess_an_installer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recognized: bool
) -> None:
    prefix = tmp_path / "a13n-harness-ui"
    prefix.mkdir()
    if recognized:
        (prefix / "uv-receipt.toml").touch()
    monkeypatch.setattr(updater.sys, "prefix", str(prefix))
    monkeypatch.setattr(updater.shutil, "which", lambda name: None if recognized else "/bin/uv")

    def unexpected(*args, **kwargs):
        pytest.fail("Unsupported installations must never invoke an installer")

    monkeypatch.setattr(updater.subprocess, "run", unexpected)
    result = CliRunner().invoke(cli, ["update"])
    assert result.exit_code == 1
    assert "original package manager" in result.output
    assert "uv tool upgrade a13n-harness-ui" in result.output


@pytest.mark.parametrize("error", [OSError("uv could not start"), KeyboardInterrupt()])
def test_explicit_update_reports_launch_failure_or_interruption_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    monkeypatch.setattr(updater, "update_command", lambda: updater.UpdateCommand("uv", tmp_path))
    calls = []

    def install(*args, **kwargs):
        calls.append(args)
        raise error

    monkeypatch.setattr(updater.subprocess, "run", install)
    result = CliRunner().invoke(cli, ["update"])
    assert result.exit_code == (130 if isinstance(error, KeyboardInterrupt) else 1)
    assert len(calls) == 1
    assert "Update complete" not in result.output
    message = "Update interrupted" if isinstance(error, KeyboardInterrupt) else "Could not run uv"
    assert message in result.output
