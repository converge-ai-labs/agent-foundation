"""Update detection, consent, and terminal ownership across startup boundaries."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from a13n_harness_ui import terminal, updater
from a13n_harness_ui.cli import CliRequest, cli
from a13n_harness_ui.interactive import startup, updates
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
    assert command == updater.UpdateCommand("/bin/uv", prefix.parent)
    assert command.argv == ("/bin/uv", "tool", "upgrade", "a13n-harness-ui")
    monkeypatch.setattr(updater.shutil, "which", lambda name: None)
    assert updates.update_command() is None


@pytest.mark.parametrize("exit_code", [0, 7])
def test_installer_runs_only_after_async_terminal_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], exit_code: int
) -> None:
    events = []
    command = updater.UpdateCommand("uv", tmp_path)

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
@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
@pytest.mark.parametrize("exit_code", [0, 7, -15])
def test_explicit_update_runs_without_terminal_setup_or_cached_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str], platform: str, exit_code: int
) -> None:
    monkeypatch.setattr(updater.sys, "platform", platform)
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
    assert "macOS computer use:" not in result.output
    assert "#restore-macos-permissions-after-an-upgrade" not in result.output


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
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    calls = []

    def install(*args, **kwargs):
        calls.append(args)
        raise error

    monkeypatch.setattr(updater.subprocess, "run", install)
    result = CliRunner().invoke(cli, ["update"])
    assert result.exit_code == (130 if isinstance(error, KeyboardInterrupt) else 1)
    assert len(calls) == 1
    assert "Update complete" not in result.output
    assert "macOS computer use:" not in result.output
    message = "Update interrupted" if isinstance(error, KeyboardInterrupt) else "Could not run uv"
    assert message in result.output
