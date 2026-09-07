"""Real PTY input/output tests with no account or provider access."""

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
    try:
        process = subprocess.Popen(
            [sys.executable, "-c", script], stdin=slave, stdout=slave, stderr=slave, cwd=tmp_path, env=env
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
    configuration = tmp_path / ".a13n-ui"
    configuration.mkdir()
    (configuration / "a13n-ui.yaml").write_text('schema_version: "2"\nprocess:\n  pricing_auto_update: false\n')
    process, master = _spawn(f"from a13n_ui.cli import main; main({command!r})", tmp_path)
    try:
        output = _read_until(master, b"Connect a model")
        assert output.count(b"\x1b[?1049h") == 1
        os.write(master, b"\x1b[B\x1b[B\r")
        output += _read_until(master, b"API model")
        os.write(master, b"\r")
        output += _read_until(master, b"Credential reference")
        os.write(master, b"\r")
        output += _read_until(master, b"Execution permissions")
        os.write(master, b"\r")
        output += _read_until(master, b"Save this configuration?")
        assert b"\x1b[?1049l" not in output
        assert output.count(b"\x1b[?1049h") == 1
        os.write(master, b"\r")
        if not command:
            output += _read_until(master, b"Enter sends a message")
            assert output.count(b"\x1b[?1049h") == 2
            os.write(master, b"/quit\r")
            output += _read_until(master, b"\x1b[?1049l")
        else:
            output += _read_until(master, b"Configuration saved")
            assert output.count(b"\x1b[?1049h") == 1
        process.wait(timeout=5)
        assert process.returncode == 0
        assert (configuration / "models").exists()
    finally:
        _stop(process, master)


def test_cancel_initial_setup_never_opens_chat(tmp_path: Path) -> None:
    process, master = _spawn("from a13n_ui.cli import main; main([])", tmp_path)
    try:
        output = _read_until(master, b"Connect a model")
        os.write(master, b"\x03")
        output += _read_until(master, b"Setup cancelled")
        process.wait(timeout=5)
        assert process.returncode == 0
        assert output.count(b"\x1b[?1049h") == 1
        assert not (tmp_path / ".a13n-ui/models").exists()
    finally:
        _stop(process, master)


def test_chat_paste_enter_steering_mode_switch_and_cancel_use_one_terminal(tmp_path: Path) -> None:
    script = r"""
import asyncio, json, time
from contextlib import asynccontextmanager
from pathlib import Path
from a13n_ui.cli import CliRequest
from a13n_ui.interactive.startup import run_terminal

class Backend:
    def __init__(self, status):
        self.status = status
        self.stop = asyncio.Event()
        self.receipt_id = None
    async def initialize(self):
        self.status.model = "fixture-model"
        return True
    async def execute(self, renderer, *, prompt=None, flush=None, admitted=None):
        Path("submitted.json").write_text(json.dumps(prompt))
        if admitted is not None:
            admitted()
        self.receipt_id = "receipt-fixture"
        renderer.ingest("TEXT_MESSAGE_CONTENT", {"delta": "fixture-stream\n"})
        await asyncio.sleep(.7)
        renderer.ingest("TOOL_CALL_START", {"tool_call_id": "edit", "tool_call_name": "edit"})
        renderer.ingest("TOOL_CALL_ARGS", {"tool_call_id": "edit", "delta": '{"file_path":"fixture.py"}'})
        renderer.ingest("TOOL_CALL_END", {"tool_call_id": "edit"})
        await self.stop.wait()
        Path("cancelled").touch()
        return "fixture-cancelled"
    async def steer(self, text, *, receipt_id):
        Path("steering.json").write_text(json.dumps([text, receipt_id]))
        return "Guidance accepted"
    async def interaction(self):
        return None
    async def cancel(self):
        self.stop.set()

@asynccontextmanager
async def factory(request, directory, status, emit):
    yield Backend(status)

def load():
    time.sleep(1.2)
    return factory

asyncio.run(run_terminal(CliRequest(), runtime_loader=load))
"""
    process, master = _spawn(script, tmp_path)
    try:
        output = _read_until(master, b"Enter sends a message")
        os.write(master, b"draft")
        assert not (tmp_path / "submitted.json").exists()
        os.write(master, b"\x1b[200~line1\nline2\x1b[201~")
        time.sleep(0.1)
        assert not (tmp_path / "submitted.json").exists()
        os.write(master, b"\r")
        output += _read_until(master, b"fixture-stream")
        assert json.loads((tmp_path / "submitted.json").read_text()) == "draftline1\nline2"
        os.write(master, b"/mode detailed\r")
        output += _read_until(master, b"fixture.py")
        # The argument payload only appears in detailed mode; terminal diffing may split its heading.
        os.write(master, b"change direction\r")
        output += _read_until(master, b"Guidance accepted")
        assert json.loads((tmp_path / "steering.json").read_text()) == ["change direction", "receipt-fixture"]
        os.write(master, b"\x03")
        output += _read_until(master, b"Enter to send")
        assert (tmp_path / "cancelled").exists()
        os.write(master, b"/quit\r")
        output += _read_until(master, b"\x1b[?1049l")
        process.wait(timeout=5)
        assert process.returncode == 0
        assert b"\x1b[?1049h" in output
        assert b"\x1b[?1049l" in output
    finally:
        _stop(process, master)
