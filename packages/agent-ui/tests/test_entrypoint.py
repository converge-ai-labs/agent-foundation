import os
import subprocess
import sys
import time
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
def test_terminal_frontend_requires_interactive_input_and_output(
    tmp_path: Path,
    command: tuple[str, ...],
) -> None:
    settings = _write_settings(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "a13n_ui", "--config", str(settings), *command],
        input="",
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 1
    assert "tui_tty_required" in result.stderr
    assert "a13n-ui run <prompt>" in result.stderr


@pytest.mark.skipif(os.name != "posix", reason="PTY terminal restoration requires POSIX")
def test_terminal_entrypoint_exits_cleanly_and_restores_pty(tmp_path: Path) -> None:
    import pty
    import select
    import termios

    settings = _write_settings(tmp_path)
    master, slave = pty.openpty()
    original = termios.tcgetattr(slave)
    process = subprocess.Popen(
        [sys.executable, "-m", "a13n_ui", "--config", str(settings)],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env={
            **os.environ,
            "A13N_UI_DATA_ROOT": str(tmp_path / "state"),
            "TERM": "xterm-256color",
        },
        close_fds=True,
    )
    output = bytearray()
    try:
        startup_deadline = time.monotonic() + 10
        while b"Agent UI" not in output and process.poll() is None:
            if time.monotonic() >= startup_deadline:
                break
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                output.extend(os.read(master, 65536))
        assert b"Agent UI" in output, output.decode(errors="replace")

        os.write(master, b"\x03")
        exit_deadline = time.monotonic() + 10
        while process.poll() is None and time.monotonic() < exit_deadline:
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                try:
                    output.extend(os.read(master, 65536))
                except OSError:
                    break
        assert process.poll() is not None, output.decode(errors="replace")
        assert process.returncode == 0, output.decode(errors="replace")
        assert termios.tcgetattr(slave) == original
        assert b"tui_tty_required" not in output
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        os.close(master)
        os.close(slave)


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
