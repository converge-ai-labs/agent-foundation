from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path

import pytest
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.host_terminal import HostTerminal, TerminalCommand, TerminalCreate, TerminalSession
from anyio import fail_after, sleep

pytestmark = [pytest.mark.anyio, pytest.mark.skipif(os.name != "posix", reason="Native POSIX PTY")]


async def output_until(session: TerminalSession, needle: bytes) -> bytes:
    with fail_after(5):
        while True:
            changed = session.changed
            output = bytes(session.output)
            if needle in output:
                return output
            await changed.wait()


async def write(session: TerminalSession, participant: str, text: str) -> None:
    await session.command(participant, TerminalCommand(kind="input", control_epoch=session.control_epoch, text=text))


async def test_real_pty_resize_interrupt_exit_and_detached_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHELL", "/bin/bash")
    terminals = HostTerminal(enabled=True)
    try:
        created = await terminals.create(TerminalCreate(cwd=str(tmp_path)))
        session = terminals.get(created.terminal_id)
        first, second = session.attach(), session.attach()
        await session.command(first, TerminalCommand(kind="control", control_epoch=0))
        await write(session, first, "stty -echo; printf '__%s__\\n' READY\n")
        await output_until(session, b"__READY__")
        await session.command(first, TerminalCommand(kind="resize", control_epoch=1, rows=37, columns=111))
        await write(session, first, "stty size\n")
        await output_until(session, b"37 111")
        await write(session, first, "bash -c 'printf \"__%s__\" SLEEPING; exec sleep 30'\n")
        await output_until(session, b"__SLEEPING__")
        await write(session, first, "\x03")
        await write(session, first, "printf '__%s__\\n' INTERRUPTED\n")
        await output_until(session, b"__INTERRUPTED__")
        session.detach(first)
        assert session.view().state == "running" and session.controller is None
        await session.command(second, TerminalCommand(kind="control", control_epoch=2))
        await write(session, second, "printf '__%s__\\n' DETACHED\n")
        await output_until(session, b"__DETACHED__")
        third = session.attach()
        assert b"__DETACHED__" in base64.b64decode(session.frame(third, 0).data_base64)
        await write(session, second, "exit 7\n")
        with fail_after(5):
            await session._waiter
        assert session.view().exit_code == 7 and session.view().state == "exited"
    finally:
        await terminals.close()
    assert not session._fd_open


async def test_controller_cas_race_and_output_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from a13n_harness_ui import host_terminal

    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setattr(host_terminal, "OUTPUT_BYTES", 1024)
    terminals = HostTerminal(enabled=True)
    try:
        session = terminals.get((await terminals.create(TerminalCreate(cwd=str(tmp_path)))).terminal_id)
        first, second = session.attach(), session.attach()
        results = await asyncio.gather(
            session.command(first, TerminalCommand(kind="control", control_epoch=0)),
            session.command(second, TerminalCommand(kind="control", control_epoch=0)),
            return_exceptions=True,
        )
        assert sum(isinstance(result, HarnessUiError) for result in results) == 1
        assert session.controller == first
        await session.command(second, TerminalCommand(kind="control", control_epoch=1))
        for kind in ("input", "resize"):
            with pytest.raises(HarnessUiError, match="control changed"):
                await session.command(first, TerminalCommand(kind=kind, control_epoch=1, text="never\n"))
        await write(session, second, "stty -echo; head -c 4096 /dev/zero | tr '\\0' x; printf '__%s__\\n' DONE\n")
        await output_until(session, b"__DONE__")
        frame = session.frame(first, 0)
        assert frame.gap and frame.start > 0 and len(base64.b64decode(frame.data_base64)) <= 1024
        assert not session.frame(first, frame.end).gap
        assert session.frame(first, frame.end + 1).gap
    finally:
        await terminals.close()


async def test_close_interrupts_foreground_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/bash")
    terminals = HostTerminal(enabled=True)
    session = terminals.get((await terminals.create(TerminalCreate(cwd=str(tmp_path)))).terminal_id)
    try:
        participant = session.attach()
        await session.command(participant, TerminalCommand(kind="control", control_epoch=0))
        await write(session, participant, "stty -echo; sleep 60 & echo $! > job.pid; printf '__%s__' JOB_READY; wait\n")
        await output_until(session, b"__JOB_READY__")
        job = int((tmp_path / "job.pid").read_text())
        closed = await terminals.remove(session.id)
        assert closed.state == "closed" and session.process.returncode is not None
        with fail_after(5):
            while True:
                try:
                    os.kill(job, 0)
                except ProcessLookupError:
                    break
                await sleep(0.01)
        assert terminals.list() == ()
    finally:
        await terminals.close()


async def test_takeover_unregisters_blocked_writer_before_new_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHELL", "/bin/bash")
    terminals = HostTerminal(enabled=True)
    pending: asyncio.Task[None] | None = None
    try:
        session = terminals.get((await terminals.create(TerminalCreate(cwd=str(tmp_path)))).terminal_id)
        first, second = session.attach(), session.attach()
        await session.command(first, TerminalCommand(kind="control", control_epoch=0))
        await write(
            session,
            first,
            "stty raw -echo; printf '__%s__' RAW; while [ ! -e release-input ]; do sleep 0.01; done; cat\n",
        )
        await output_until(session, b"__RAW__")
        blocked = asyncio.Event()
        loop = asyncio.get_running_loop()
        add_writer = loop.add_writer

        def registered_writer(fd, callback, *args):
            add_writer(fd, callback, *args)
            if fd == session.master:
                blocked.set()

        monkeypatch.setattr(loop, "add_writer", registered_writer)

        async def paste() -> None:
            for _ in range(8):
                await session.command(first, TerminalCommand(kind="input", control_epoch=1, text="x" * 16384))

        pending = asyncio.create_task(paste())
        with fail_after(10):
            await blocked.wait()
        assert not pending.done()
        await session.command(second, TerminalCommand(kind="control", control_epoch=1))
        (tmp_path / "release-input").touch()
        # Without coordination, anyio raises BusyResourceError here because the
        # old controller still owns the master FD's writable registration.
        await session.command(second, TerminalCommand(kind="input", control_epoch=2, text="new controller\n"))
        with pytest.raises(HarnessUiError, match="control changed"):
            await pending
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await terminals.close()


async def test_close_sweeps_jobs_spawned_by_term_handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import psutil

    monkeypatch.setenv("SHELL", "/bin/bash")
    terminals = HostTerminal(enabled=True)
    session = terminals.get((await terminals.create(TerminalCreate(cwd=str(tmp_path)))).terminal_id)
    try:
        participant = session.attach()
        await session.command(participant, TerminalCommand(kind="control", control_epoch=0))
        await write(
            session,
            participant,
            'bash -c \'trap : TERM; trap "" HUP; printf "__%s__" STARTED; while :; do sleep 30; done\' &\n',
        )
        await output_until(session, b"__STARTED__")
        await terminals.remove(session.id)
        remaining = []
        for process in psutil.process_iter():
            try:
                if os.getsid(process.pid) == session.process.pid and process.status() != psutil.STATUS_ZOMBIE:
                    remaining.append(process)
            except (ProcessLookupError, PermissionError, psutil.NoSuchProcess):
                pass
        # Cleanup even if this regression fails; no fixture process may escape.
        for process in remaining:
            process.kill()
        assert not remaining
    finally:
        await terminals.close()


async def test_immediate_close_reaps_launching_child(tmp_path: Path) -> None:
    terminals = HostTerminal(enabled=True)
    for _ in range(5):
        created = await terminals.create(TerminalCreate(cwd=str(tmp_path)))
        session = terminals.get(created.terminal_id)
        await terminals.remove(created.terminal_id)
        assert session.process.returncode is not None and not session._fd_open
    await terminals.close()


@pytest.mark.parametrize("cwd", ["relative", "/missing-terminal-directory-for-test", "bad\x00path"])
async def test_terminal_requires_an_existing_absolute_directory(cwd: str) -> None:
    terminals = HostTerminal(enabled=True)
    with pytest.raises(HarnessUiError) as failure:
        await terminals.create(TerminalCreate(cwd=cwd))
    assert failure.value.code == "host_terminal_cwd_invalid"
    assert terminals.list() == ()
    await terminals.close()
