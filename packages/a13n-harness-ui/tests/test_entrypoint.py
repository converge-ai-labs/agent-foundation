import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# These are functional smoke tests, not startup benchmarks. Leave room for
# cold imports and local storage initialization on hosted Windows runners.
_ENTRYPOINT_TIMEOUT_SECONDS = 60


def test_module_entrypoint_exposes_cli_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "a13n_harness_ui", "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=_ENTRYPOINT_TIMEOUT_SECONDS,
    )

    assert result.returncode == 0
    assert "runtime" not in result.stdout
    assert "--config" in result.stdout


def test_startup_view_imports_without_loading_execution_dependencies() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "import a13n_harness_ui.interactive.startup; "
            "import a13n_harness_ui.interactive.onboarding; "
            "import a13n_harness_ui.interactive.updates; "
            "assert not {'a13n_harness', 'pydantic_ai', 'a13n_harness_ui.app', 'a13n_harness_ui.storage'} & sys.modules.keys()",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=_ENTRYPOINT_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, result.stderr


def test_terminal_frontend_requires_interactive_input_and_output(
    tmp_path: Path,
) -> None:
    settings = _write_settings(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "a13n_harness_ui", "--config", str(settings)],
        input="",
        check=False,
        capture_output=True,
        text=True,
        timeout=_ENTRYPOINT_TIMEOUT_SECONDS,
    )

    assert result.returncode == 1
    assert "Interactive mode requires a terminal" in result.stderr
    assert "a13n-harness-ui run <prompt>" in result.stderr


@pytest.mark.skipif(os.name != "posix", reason="PTY terminal restoration requires POSIX")
def test_terminal_entrypoint_exits_cleanly_and_restores_pty(tmp_path: Path) -> None:
    import pty
    import select
    import termios

    settings = _write_settings(tmp_path)
    master, slave = pty.openpty()
    original = termios.tcgetattr(slave)
    process = subprocess.Popen(
        [sys.executable, "-m", "a13n_harness_ui", "--config", str(settings)],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env={
            **os.environ,
            "A13N_HARNESS_UI_DATA_ROOT": str(tmp_path / "state"),
            "TERM": "xterm-256color",
            "HOME": str(tmp_path),
            "CODEX_HOME": str(tmp_path / "codex"),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        },
        close_fds=True,
    )
    output = bytearray()
    try:
        startup_deadline = time.monotonic() + _ENTRYPOINT_TIMEOUT_SECONDS
        while b"Connect a model" not in output and process.poll() is None:
            if time.monotonic() >= startup_deadline:
                break
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                output.extend(os.read(master, 65536))
        assert b"Connect a model" in output, output.decode(errors="replace")

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
        restored = termios.tcgetattr(slave)
        # BSD/macOS may set PENDIN when queued PTY input is reprocessed after
        # raw mode ends. It is transient line-discipline state, not an input
        # mode the Application owns. Compare every other flag and control byte.
        restored[3] &= ~termios.PENDIN
        original[3] &= ~termios.PENDIN
        assert restored == original
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


def _write_settings(tmp_path: Path) -> Path:
    settings = tmp_path / "settings.yaml"
    settings.write_text('schema_version: "2"\n')
    return settings
