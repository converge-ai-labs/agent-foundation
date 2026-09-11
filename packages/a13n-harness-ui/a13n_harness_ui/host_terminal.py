"""App-lived native PTYs; bounded observation, never a durable terminal log."""

from __future__ import annotations

import asyncio
import base64
import errno
import os
import shutil
import signal
import sys
from contextlib import suppress
from pathlib import Path
from typing import Literal
from uuid import uuid4

import psutil
from anyio import CancelScope, fail_after, to_thread, wait_writable
from pydantic import Field

from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.host_files import NativePath
from a13n_harness_ui.surfaces import SurfaceModel

if os.name == "posix":
    import termios

OUTPUT_BYTES = 1024 * 1024
MAX_TERMINALS = 32


class TerminalSize(SurfaceModel):
    rows: int = Field(default=24, ge=1, le=1000)
    columns: int = Field(default=80, ge=1, le=1000)


class TerminalCreate(TerminalSize):
    cwd: NativePath
    project_id: str | None = Field(default=None, max_length=200)


class TerminalView(TerminalSize):
    terminal_id: str
    cwd: str
    project_id: str | None
    shell: str
    state: Literal["running", "exited", "closed"]
    exit_code: int | None
    controller: str | None
    control_epoch: int
    participants: tuple[str, ...]
    output_start: int
    output_end: int


class TerminalCommand(TerminalSize):
    kind: Literal["control", "input", "resize"]
    control_epoch: int = Field(ge=0)
    release: bool = False
    text: str = Field(default="", max_length=16384)


class TerminalFrame(SurfaceModel):
    kind: Literal["terminal"] = "terminal"
    participant_id: str
    terminal: TerminalView
    start: int
    end: int
    gap: bool
    data_base64: str


def _failure(message: str, code: str) -> HarnessUiError:
    return HarnessUiError(message, code="host_terminal_" + code)


def _working_directory(value: str) -> Path:
    try:
        value.encode("utf-8")
        path = Path(value)
        if "\x00" in value or not path.is_absolute():
            raise ValueError("Not an absolute native path")
        path = path.resolve(strict=True)
        if not path.is_dir():
            raise ValueError("Not a directory")
        return path
    except (ValueError, OSError):
        raise _failure("Select an existing absolute native directory.", "cwd_invalid") from None


def _stop_jobs(session_id: int, force: bool = False) -> None:
    # Job control uses multiple process groups. Enumerate this native session,
    # not just the shell's foreground group, while keeping the shell alive to
    # reap children. Deliberately daemonized/new-session processes are not PTYs.
    jobs: list[psutil.Process] = []
    for process in psutil.process_iter():
        if process.pid == session_id:
            continue
        with suppress(ProcessLookupError, PermissionError, psutil.NoSuchProcess):
            if os.getsid(process.pid) == session_id:
                jobs.append(process)
    for process in jobs:
        with suppress(psutil.NoSuchProcess):
            if force:
                process.kill()
            else:
                process.terminate()
    _, alive = psutil.wait_procs(jobs, timeout=0.3)
    for process in alive:
        with suppress(psutil.NoSuchProcess):
            process.kill()
    psutil.wait_procs(alive, timeout=0.3)


class TerminalSession:
    def __init__(self, request: TerminalCreate, shell: str, master: int, process: asyncio.subprocess.Process) -> None:
        self.id = "terminal-" + uuid4().hex
        self.request = request
        self.shell = shell
        self.master = master
        self.process = process
        self.rows, self.columns = request.rows, request.columns
        self.participants: set[str] = set()
        self.controller: str | None = None
        self.control_epoch = 0
        self.output = bytearray()
        self.end = 0
        self.state: Literal["running", "exited", "closed"] = "running"
        self.changed = asyncio.Event()
        self._fd_open = True
        self._close_lock = asyncio.Lock()
        self._input_lock = asyncio.Lock()
        self._input_scope: CancelScope | None = None
        asyncio.get_running_loop().add_reader(master, self._read)
        self._waiter = asyncio.create_task(self._wait(), name=self.id)

    def _cancel_input(self) -> None:
        if self._input_scope is not None:
            self._input_scope.cancel()

    def _notify(self) -> None:
        self.changed.set()
        self.changed = asyncio.Event()

    def _read(self) -> None:
        try:
            data = os.read(self.master, 65536)
        except BlockingIOError:
            return
        except OSError as exc:
            if exc.errno != errno.EIO:
                raise
            data = b""
        if not data:
            asyncio.get_running_loop().remove_reader(self.master)
            return
        self.output.extend(data)
        self.end += len(data)
        if len(self.output) > OUTPUT_BYTES:
            del self.output[:-OUTPUT_BYTES]
        self._notify()

    async def _wait(self) -> None:
        await self.process.wait()
        if self.state == "running":
            self.state = "exited"
        self._notify()

    def view(self) -> TerminalView:
        return TerminalView(
            terminal_id=self.id,
            cwd=self.request.cwd,
            project_id=self.request.project_id,
            shell=self.shell,
            rows=self.rows,
            columns=self.columns,
            state=self.state,
            exit_code=self.process.returncode,
            controller=self.controller,
            control_epoch=self.control_epoch,
            participants=tuple(sorted(self.participants)),
            output_start=self.end - len(self.output),
            output_end=self.end,
        )

    def attach(self) -> str:
        participant = "participant-" + uuid4().hex
        self.participants.add(participant)
        self._notify()
        return participant

    def detach(self, participant: str) -> None:
        self.participants.discard(participant)
        if self.controller == participant:
            self.controller = None
            self.control_epoch += 1
            self._cancel_input()
        self._notify()

    def frame(self, participant: str, cursor: int) -> TerminalFrame:
        first = self.end - len(self.output)
        start = max(first, min(cursor, self.end))
        return TerminalFrame(
            participant_id=participant,
            terminal=self.view(),
            start=start,
            end=self.end,
            gap=cursor < first or cursor > self.end,
            data_base64=base64.b64encode(self.output[start - first :]).decode("ascii"),
        )

    def _check_control(self, participant: str, epoch: int) -> None:
        if self.state != "running":
            raise _failure("The terminal process is no longer running.", "exited")
        if participant not in self.participants or epoch != self.control_epoch:
            raise _failure("Terminal control changed; inspect the current controller.", "control_conflict")

    async def command(self, participant: str, command: TerminalCommand) -> None:
        self._check_control(participant, command.control_epoch)
        if command.kind == "control":
            if command.release and self.controller != participant:
                raise _failure("Only the current controller may release control.", "control_conflict")
            self.controller = None if command.release else participant
            self.control_epoch += 1
            self._cancel_input()
            self._notify()
            return
        if self.controller != participant:
            raise _failure("Acquire terminal control before input or resize.", "control_conflict")
        if command.kind == "resize":
            termios.tcsetwinsize(self.master, (command.rows, command.columns))
            self.rows, self.columns = command.rows, command.columns
            self._notify()
            return
        # A takeover cancels the old scope. The lock lets its FD waiter finish
        # unregistering before the new controller can register another writer.
        async with self._input_lock:
            scope = CancelScope()
            self._input_scope = scope
            try:
                with scope:
                    await self._write(participant, command)
            finally:
                self._input_scope = None
            if scope.cancel_called:
                raise _failure("Terminal control changed; input may be partial. Do not replay.", "control_conflict")

    async def _write(self, participant: str, command: TerminalCommand) -> None:
        data = command.text.encode("utf-8")
        while data:
            # No await between checking ownership and writing. Backpressure may
            # yield; a takeover then rejects the remaining bytes, never retries.
            self._check_control(participant, command.control_epoch)
            if self.controller != participant:
                raise _failure("Terminal control changed during input.", "control_conflict")
            try:
                written = os.write(self.master, data)
            except BlockingIOError:
                try:
                    with fail_after(1):
                        await wait_writable(self.master)
                except (TimeoutError, OSError):
                    raise _failure(
                        "Terminal input stalled; delivery may be partial. Do not replay.", "input_failed"
                    ) from None
                continue
            except OSError:
                raise _failure(
                    "Terminal input stopped; delivery may be partial. Do not replay.", "input_failed"
                ) from None
            data = data[written:]

    async def close(self) -> TerminalView:
        with CancelScope(shield=True):
            async with self._close_lock:
                if self.state == "closed":
                    return self.view()
                self.state = "closed"
                self.controller = None
                self.control_epoch += 1
                self._cancel_input()
                self._notify()
                await to_thread.run_sync(_stop_jobs, self.process.pid)
                # Closing the controlling PTY also hangs up the session.
                if self._fd_open:
                    with suppress(OSError):
                        foreground = os.tcgetpgrp(self.master)
                        if foreground > 0 and foreground != os.getpgrp():
                            os.killpg(foreground, signal.SIGHUP)
                    with suppress(ProcessLookupError):
                        os.killpg(self.process.pid, signal.SIGHUP)
                    asyncio.get_running_loop().remove_reader(self.master)
                    os.close(self.master)
                    self._fd_open = False
                try:
                    await asyncio.wait_for(asyncio.shield(self._waiter), 1)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        self.process.kill()
                    await self._waiter
                # TERM handlers can create another job during the first sweep.
                # All known parents and the shell are now gone; kill remaining
                # members of the original session without another TERM window.
                await to_thread.run_sync(_stop_jobs, self.process.pid, True)
        return self.view()


class HostTerminal:
    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self.sessions: dict[str, TerminalSession] = {}
        self._closed = False
        self._create_lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self.enabled and os.name == "posix"

    def require_enabled(self) -> None:
        if not self.enabled:
            raise _failure("Native computer sharing is disabled.", "disabled")
        if not self.available:
            raise _failure("This Host does not support native POSIX PTYs.", "unavailable")
        if self._closed:
            raise _failure("The terminal service is closed.", "unavailable")

    def list(self) -> tuple[TerminalView, ...]:
        self.require_enabled()
        return tuple(session.view() for session in self.sessions.values())

    def get(self, terminal_id: str) -> TerminalSession:
        self.require_enabled()
        session = self.sessions.get(terminal_id)
        if session is None:
            raise _failure("The terminal does not exist in this App lifetime.", "not_found")
        return session

    async def create(self, request: TerminalCreate) -> TerminalView:
        session: TerminalSession | None = None
        with CancelScope(shield=True):
            async with self._create_lock:
                self.require_enabled()
                if len(self.sessions) >= MAX_TERMINALS:
                    raise _failure("Close an existing terminal before creating another.", "limit")
                cwd = await to_thread.run_sync(_working_directory, request.cwd)
                shell = shutil.which(os.environ.get("SHELL", "bash")) or shutil.which("sh")
                if shell is None:
                    raise _failure("No native interactive shell is available.", "unavailable")
                try:
                    master, slave = os.openpty()
                except OSError:
                    raise _failure("The Host could not allocate a native PTY.", "unavailable") from None
                try:
                    termios.tcsetwinsize(slave, (request.rows, request.columns))
                    os.set_blocking(master, False)
                    process = await asyncio.create_subprocess_exec(
                        sys.executable,
                        str(Path(__file__).with_name("_pty_child.py")),
                        str(slave),
                        shell,
                        pass_fds=(slave,),
                        cwd=cwd,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                        env={**os.environ, "TERM": "xterm-256color"},
                    )
                except OSError:
                    os.close(master)
                    raise _failure("The native terminal could not be started.", "unavailable") from None
                except BaseException:
                    os.close(master)
                    raise
                finally:
                    os.close(slave)
                session = TerminalSession(request.model_copy(update={"cwd": str(cwd)}), shell, master, process)
                self.sessions[session.id] = session
        assert session is not None
        return session.view()

    async def remove(self, terminal_id: str) -> TerminalView:
        session = self.get(terminal_id)
        view = await session.close()
        self.sessions.pop(terminal_id, None)
        return view

    async def close(self) -> None:
        with CancelScope(shield=True):
            async with self._create_lock:
                self._closed = True
                await asyncio.gather(*(session.close() for session in self.sessions.values()))
                self.sessions.clear()
