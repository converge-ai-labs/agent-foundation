import subprocess
import sys
from pathlib import Path

import pytest


def test_module_entrypoint_exposes_cli_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "a13n_ui", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "runtime" not in result.stdout
    assert "--config" in result.stdout


@pytest.mark.parametrize("command", [(), ("tui",)])
def test_terminal_frontend_uses_process_local_app(tmp_path: Path, command: tuple[str, ...]) -> None:
    settings = _write_settings(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "a13n_ui", "--config", str(settings), *command],
        input="/status\n/exit\n",
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "Agent UI TUI." in result.stdout
    assert "ready objects=" in result.stdout
    assert "runtime-" not in result.stdout


def test_environment_list_exposes_release_owned_modes(tmp_path: Path) -> None:
    settings = _write_settings(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "a13n_ui",
            "--config",
            str(settings),
            "environment",
            "list",
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert '"profile_id":"environment-native"' in result.stdout
    assert '"profile_id":"environment-sandbox"' in result.stdout
    assert '"mode":"full-control"' in result.stdout
    assert '"mode":"sandbox"' in result.stdout


def _write_settings(tmp_path: Path) -> Path:
    settings = tmp_path / "settings.yaml"
    settings.write_text('schema_version: "2"\n')
    return settings
