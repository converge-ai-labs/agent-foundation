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


def test_real_app_landing_is_editable_before_preparation_and_restores_alternate_screen(tmp_path: Path) -> None:
    configuration = tmp_path / ".a13n-ui"
    configuration.mkdir()
    (configuration / "a13n-ui.yaml").write_text('schema_version: "2"\nprocess:\n  pricing_auto_update: false\n')
    started = time.monotonic()
    process, master = _spawn("from a13n_ui.cli import main; main([])", tmp_path)
    try:
        output = _read_until(master, b">")
        # Generous CI ceiling; the architectural import-isolation test is primary.
        assert time.monotonic() - started < 1.5
        os.write(master, b"/help\r")
        output += _read_until(master, b"Bracketed multiline paste")
        output += _read_until(master, b"Connect a model")
        os.write(master, b"\x1b[B\r")
        output += _read_until(master, b"API model route")
        os.write(master, b"/cancel\r")
        output += _read_until(master, b"Setup cancelled")
        os.write(master, b"/quit\r")
        output += _read_until(master, b"\x1b[?1049l")
        process.wait(timeout=5)
        assert process.returncode == 0
        assert b"\x1b[?1049h" in output
        assert b"\x1b[?1049l" in output
        assert not (configuration / "models").exists()
    finally:
        _stop(process, master)


def test_startup_draft_paste_mode_switch_and_cancel_use_one_terminal(tmp_path: Path) -> None:
    script = r"""
import asyncio, json, time
from contextlib import asynccontextmanager
from pathlib import Path
from a13n_ui.cli import CliRequest
from a13n_ui.interactive.shell import CliShell

class Backend:
    def __init__(self, status):
        self.status = status
        self.stop = asyncio.Event()
    async def initialize(self):
        self.status.model = "fixture-model"
        return True
    async def execute(self, renderer, *, prompt=None, flush=None, admitted=None):
        Path("submitted.json").write_text(json.dumps(prompt))
        if admitted is not None:
            admitted()
        renderer.ingest("TEXT_MESSAGE_CONTENT", {"delta": "fixture-stream\n"})
        await asyncio.sleep(.7)
        renderer.ingest("TOOL_CALL_START", {"tool_call_id": "edit", "tool_call_name": "edit"})
        renderer.ingest("TOOL_CALL_ARGS", {"tool_call_id": "edit", "delta": '{"file_path":"fixture.py"}'})
        renderer.ingest("TOOL_CALL_END", {"tool_call_id": "edit"})
        await self.stop.wait()
        return "fixture-cancelled"
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

asyncio.run(CliShell(CliRequest(), runtime_loader=load).run())
"""
    process, master = _spawn(script, tmp_path)
    try:
        output = _read_until(master, b">")
        os.write(master, b"draft\r")
        output += _read_until(master, b"Your draft is preserved")
        output += _read_until(master, b"Ready")
        assert not (tmp_path / "submitted.json").exists()
        os.write(master, b"\x1b[200~line1\nline2\x1b[201~")
        time.sleep(0.1)
        assert not (tmp_path / "submitted.json").exists()
        os.write(master, b"\r")
        output += _read_until(master, b"fixture-stream")
        assert json.loads((tmp_path / "submitted.json").read_text()) == "draftline1\nline2"
        os.write(master, b"/mode detailed\r")
        output += _read_until(master, b"fixture.py")
        assert b"[Tool] edit" in output
        os.write(master, b"\x03")
        output += _read_until(master, b"fixture-cancelled")
        os.write(master, b"/quit\r")
        output += _read_until(master, b"\x1b[?1049l")
        process.wait(timeout=5)
        assert process.returncode == 0
        assert b"\x1b[?1049h" in output
        assert b"\x1b[?1049l" in output
    finally:
        _stop(process, master)
