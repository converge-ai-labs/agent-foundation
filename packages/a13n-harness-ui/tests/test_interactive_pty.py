"""Real PTY input/output tests with no account or provider access.

Keep the few terminal-boundary scenarios together; domain behavior is tested in-process.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX PTY contract; Windows uses prompt-toolkit's console adapter"
)


def _read_until(master: int, marker: bytes, *, timeout: float = 8) -> bytes:
    output = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if select.select([master], [], [], 0.05)[0]:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            output.extend(chunk)
            if b"\x1b[6n" in chunk:
                os.write(master, b"\x1b[1;1R")
            if marker in output:
                return bytes(output)
    raise AssertionError(f"Terminal did not produce {marker!r}: {bytes(output)[-6000:]!r}")


def _spawn(script: str, tmp_path: Path) -> tuple[subprocess.Popen[bytes], int]:
    import fcntl
    import pty
    import struct
    import termios

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "TERM": "xterm-256color",
        "CODEX_HOME": str(tmp_path / "codex"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
    }
    # Autouse fixtures do not cross the subprocess boundary. Neither catalog
    # refresh, pricing nor release checks are part of terminal rendering tests.
    offline = """
from contextlib import nullcontext
from pydantic_ai import prices
from a13n_harness_ui import model_catalog
from a13n_harness_ui.interactive import updates
prices.update_in_background = nullcontext
async def bundled():
    return model_catalog.bundled_models()
async def no_update(root):
    return None
model_catalog.fetch_directory = bundled
updates.check_update = no_update
"""
    try:
        process = subprocess.Popen(
            [sys.executable, "-c", offline + script], stdin=slave, stdout=slave, stderr=slave, cwd=tmp_path, env=env
        )
    finally:
        os.close(slave)
    return process, master


def _stop(process: subprocess.Popen[bytes], master: int) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        os.close(master)


@pytest.mark.parametrize("command", [[], ["setup"]])
def test_setup_redraws_one_alternate_screen_and_only_launch_enters_chat(tmp_path: Path, command: list[str]) -> None:
    configuration = tmp_path / ".a13n-harness-ui"
    configuration.mkdir()
    (configuration / "a13n-harness-ui.yaml").write_text('schema_version: "1"\nprocess:\n  pricing_auto_update: false\n')
    script = f"from a13n_harness_ui.cli import main; main({command!r})"
    process, master = _spawn(script, tmp_path)
    try:
        # A cold subprocess imports the complete runtime before the first screen;
        # this is a readiness guard, not an eight-second startup benchmark.
        output = _read_until(master, b"Connect a model", timeout=30)
        assert output.count(b"\x1b[?1049h") == 1
        os.write(master, b"\x1b[B\x1b[B\x1b[B\r")
        output += _read_until(master, b"API provider")
        os.write(master, b"\r")
        output += _read_until(master, b"Base URL")
        os.write(master, b"\r")
        output += _read_until(master, b"Choose API authentication")
        os.write(master, b"\r")
        output += _read_until(master, b"API key (hidden)")
        os.write(master, b"fixture-hidden-api-key\r")
        output += _read_until(master, b"Model ID")
        os.write(master, b"gpt-5.6-sol\r")
        output += _read_until(master, b"High thinking")
        os.write(master, b"\r")
        output += _read_until(master, b"Working context budget")
        os.write(master, b"\r")
        output += _read_until(master, b"Choose native Agent tools")
        os.write(master, b"\r")
        output += _read_until(master, b"Execution permissions")
        os.write(master, b"\r")
        if not command:
            output += _read_until(master, b"Enter sends a message")
            assert output.count(b"\x1b[?1049h") == 1
            assert b"\x1b[?1049l" not in output
            os.write(master, b"/quit\r")
            output += _read_until(master, b"\x1b[?1049l")
        else:
            output += _read_until(master, b"Ready. Use /agent")
            assert output.count(b"\x1b[?1049h") == 1
        process.wait(timeout=5)
        assert process.returncode == 0
        assert (configuration / "models").exists()
        assert b"fixture-hidden-api-key" not in output
        assert all(b"fixture-hidden-api-key" not in path.read_bytes() for path in configuration.rglob("*.yaml"))
    finally:
        _stop(process, master)


def test_cancel_initial_setup_never_opens_chat(tmp_path: Path) -> None:
    process, master = _spawn("from a13n_harness_ui.cli import main; main([])", tmp_path)
    try:
        # A cold subprocess imports the complete runtime before the first screen;
        # this is a readiness guard, not an eight-second startup benchmark.
        output = _read_until(master, b"Connect a model", timeout=30)
        os.write(master, b"\x03")
        output += _read_until(master, b"Setup cancelled")
        process.wait(timeout=5)
        assert process.returncode == 0
        assert output.count(b"\x1b[?1049h") == 1
        assert not (tmp_path / ".a13n-harness-ui/models").exists()
    finally:
        _stop(process, master)


@pytest.mark.parametrize("install", [False, True])
def test_update_screen_precedes_setup_and_releases_terminal_for_installer(tmp_path: Path, install: bool) -> None:
    script = r"""
from pathlib import Path
from types import SimpleNamespace
from a13n_harness_ui.cli import main
from a13n_harness_ui.interactive import updates
from a13n_harness_ui import updater
async def check(root):
    return updates.AvailableUpdate("1.0", "2.0")
updates.check_update = check
updates.update_command = lambda: updates.UpdateCommand("fixture-uv", Path.cwd())
def install(*args, **kwargs):
    print("INSTALLER STARTED", flush=True)
    return SimpleNamespace(returncode=0)
updater.subprocess.run = install
main(["setup"])
"""
    process, master = _spawn(script, tmp_path)
    try:
        output = _read_until(master, b"Install this update?", timeout=30)
        assert b"Connect a model" not in output
        assert output.count(b"\x1b[?1049h") == 1
        if install:
            os.write(master, b"\x1b[A\r")
            output += _read_until(master, b"Restart a13n-harness-ui")
            assert output.index(b"\x1b[?1049l") < output.index(b"INSTALLER STARTED")
            assert b"Connect a model" not in output
        else:
            os.write(master, b"\r")
            output += _read_until(master, b"Connect a model")
            assert output.count(b"\x1b[?1049h") == 1
            assert b"\x1b[?1049l" not in output
            os.write(master, b"\x03")
            output += _read_until(master, b"Setup cancelled")
            assert b"INSTALLER STARTED" not in output
        process.wait(timeout=5)
        assert process.returncode == 0
    finally:
        _stop(process, master)


def test_chat_paste_enter_steering_mode_switch_and_cancel_use_one_terminal(tmp_path: Path) -> None:
    pasted = "long pasted text\n" * 100
    script = r"""
import asyncio, json
from contextlib import asynccontextmanager
from pathlib import Path
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.startup import run_terminal

class Backend:
    thread_id = None
    resumed_transcript = None

    def thinking_choices(self):
        return ()

    async def skill_catalog(self):
        return None

    def __init__(self, status):
        self.status = status
        self.stop = asyncio.Event()
        self.steered = asyncio.Event()
        self.receipt_id = None
    async def initialize(self):
        self.status.model = "fixture-model"
        return True
    async def execute(self, renderer, *, prompt=None, flush=None, admitted=None, skill_references=(), mode="normal"):
        Path("submitted.json").write_text(json.dumps(prompt.text))
        if admitted is not None:
            admitted()
        self.receipt_id = "receipt-fixture"
        renderer.ingest("TEXT_MESSAGE_CONTENT", {"delta": "fixture-stream\n"})
        await self.steered.wait()
        renderer.ingest("TOOL_CALL_START", {"tool_call_id": "edit", "tool_call_name": "view"})
        renderer.ingest("TOOL_CALL_ARGS", {"tool_call_id": "edit", "delta": '{"file_path":"fixture.py"}'})
        renderer.ingest("TOOL_CALL_END", {"tool_call_id": "edit"})
        await self.stop.wait()
        Path("cancelled").touch()
        return "fixture-cancelled"
    async def steer(self, text, *, receipt_id, skill_references=()):
        Path("steering.json").write_text(json.dumps([text.text, receipt_id]))
        self.steered.set()
        return "Guidance sent"
    async def interaction(self):
        return None
    async def cancel(self):
        self.stop.set()

@asynccontextmanager
async def factory(request, directory, status, emit):
    yield Backend(status)

asyncio.run(run_terminal(CliRequest(no_update_check=True), runtime_loader=lambda: factory))
"""
    process, master = _spawn(script, tmp_path)
    try:
        output = _read_until(master, b"Enter sends a message", timeout=30)
        assert output.count(b"\x1b[?1049h") == 1
        assert b"\x1b[?1049l" not in output
        os.write(master, b"draft")
        assert not (tmp_path / "submitted.json").exists()
        os.write(master, b"\x1b[200~" + pasted.encode() + b"\x1b[201~")
        # Wait for the bracketed paste to render, not a guessed input delay.
        output += _read_until(master, b"[Pasted text #1: 1700 chars]")
        assert not (tmp_path / "submitted.json").exists()
        os.write(master, b"\r")
        output += _read_until(master, b"fixture-stream")
        assert json.loads((tmp_path / "submitted.json").read_text()) == "draft" + pasted
        os.write(master, b"/mode detailed\r")
        output += _read_until(master, b"Display \xc2\xb7 detailed")
        os.write(master, b"change direction\r")
        # The backend emits the tool only after steering has been handled.
        output += _read_until(master, b"fixture.py")
        # Detailed mode retains formatted arguments; terminal diffing may split headings.
        assert json.loads((tmp_path / "steering.json").read_text()) == ["change direction", "receipt-fixture"]
        os.write(master, b"\x03")
        output += _read_until(master, b"Enter to send")
        assert (tmp_path / "cancelled").exists()
        os.write(master, b"/quit\r")
        output += _read_until(master, b"\x1b[?1049l")
        process.wait(timeout=5)
        assert process.returncode == 0
        assert output.count(b"\x1b[?1049h") == 1
        assert output.count(b"\x1b[?1049l") == 1
    finally:
        _stop(process, master)
