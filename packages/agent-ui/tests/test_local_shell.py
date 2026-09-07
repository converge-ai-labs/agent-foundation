"""Real host processes plus reserved bang-input behavior."""

from __future__ import annotations

import asyncio
import os
import shlex
import sys
from pathlib import Path

import pytest
from a13n_ui.cli import CliRequest
from a13n_ui.interactive.local_shell import run_local_shell
from a13n_ui.interactive.rendering import Status, StreamRenderer
from a13n_ui.interactive.shell import CliShell
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

pytestmark = [pytest.mark.anyio, pytest.mark.skipif(os.name != "posix", reason="POSIX command syntax")]


async def test_host_command_streams_both_pipes_and_exit_without_model_input(tmp_path: Path) -> None:
    events = []
    renderer = StreamRenderer(Status())

    def emit(event):
        events.append(event)
        renderer.local_shell(event)

    await run_local_shell("pwd; printf 'out'; printf 'err' >&2; exit 7", tmp_path, emit)
    assert events[0].kind == "started"
    assert events[-1].phase == "exited" and events[-1].exit_code == 7
    output = "".join(event.text for event in events if event.stream == "stdout")
    assert str(tmp_path.resolve()) in output and "out" in output
    assert "".join(event.text for event in events if event.stream == "stderr") == "err"
    assert not renderer.assistant_seen
    assert "Local shell · exited · exit 7" in renderer.drain()
    renderer.transcript.close()


async def test_output_is_bounded_but_both_pipes_are_drained(tmp_path: Path) -> None:
    events = []
    code = "import os; os.write(1,b'a'*200000); os.write(2,b'b'*200000)"
    await run_local_shell(
        f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}", tmp_path, events.append, output_limit=1024
    )
    assert sum(len(event.text) for event in events) == 1024
    assert events[-1].truncated and events[-1].exit_code == 0


@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_cancel_kill_descendants(tmp_path: Path, cancel: bool) -> None:
    events = []
    pidfile = tmp_path / "child.pid"
    task = asyncio.create_task(
        run_local_shell(
            "sleep 100 & echo $! > child.pid; wait", tmp_path, events.append, timeout=0.3 if not cancel else 30
        )
    )
    async with asyncio.timeout(3):
        while not pidfile.exists():
            await asyncio.sleep(0.01)
    child = int(pidfile.read_text())
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await task
    assert events[-1].phase == ("cancelled" if cancel else "timed_out")
    # A reparented zombie may await init on Linux, but may not still be executing.
    proc = await asyncio.create_subprocess_exec("ps", "-o", "stat=", "-p", str(child), stdout=asyncio.subprocess.PIPE)
    state, _ = await proc.communicate()
    assert not state.strip() or state.strip().startswith(b"Z")


async def test_cancel_during_spawn_still_drains_full_pipes(tmp_path: Path, monkeypatch) -> None:
    from a13n_ui.interactive import local_shell

    original = asyncio.create_subprocess_shell
    spawned, release = asyncio.Event(), asyncio.Event()
    events = []

    async def delayed_spawn(*args, **kwargs):
        process = await original(*args, **kwargs)
        await asyncio.sleep(0.15)
        spawned.set()
        await release.wait()
        return process

    monkeypatch.setattr(local_shell.asyncio, "create_subprocess_shell", delayed_spawn)
    task = asyncio.create_task(run_local_shell("yes", tmp_path, events.append, output_limit=1024))
    await asyncio.wait_for(spawned.wait(), 3)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert events[-1].phase == "cancelled"


async def test_bang_while_model_busy_is_not_steering_and_preserves_draft() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.ready = True
        shell.job_kind = "run"
        shell.job = asyncio.create_task(asyncio.Event().wait())
        terminal = asyncio.create_task(shell.app.run_async())
        try:
            async with asyncio.timeout(3):
                while not shell.app.is_running:
                    await asyncio.sleep(0.01)
            pipe.send_text("!echo must-not-run\r")
            async with asyncio.timeout(3):
                while not shell.renderer.transcript.blocks:
                    await asyncio.sleep(0.01)
            assert shell.composer.text == "!echo must-not-run"
            assert shell._input_task is None
        finally:
            shell.app.exit()
            await terminal
            shell.job.cancel()
            await asyncio.gather(shell.job, return_exceptions=True)
            shell.renderer.transcript.close()


async def test_bang_launches_without_backend_and_keeps_images(tmp_path: Path) -> None:
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.ready = True
        shell.directory = tmp_path
        images = shell.images
        await shell.handle("!printf local")
        assert shell.job_kind == "local shell"
        await shell.job
        assert shell.images == images
        assert any("local" in block.source for block in shell.renderer.transcript.blocks.values())
        assert shell.backend is None
        shell.renderer.transcript.close()
