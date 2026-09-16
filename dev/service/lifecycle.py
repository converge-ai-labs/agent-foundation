"""Checkout lifecycle locking and foreground process-group supervision."""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FrameType


@contextmanager
def lifecycle_lock(root: Path, *, inheritable: bool = False) -> Iterator[int]:
    """Exclude every start, reset, and down operation for one checkout."""
    directory = root / "var/dev"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "lifecycle.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Local development is already starting, running, resetting, or stopping") from None
        os.set_inheritable(fd, inheritable)
        yield fd
    finally:
        os.close(fd)


@contextmanager
def inherited_lifecycle_lock(fd: int) -> Iterator[None]:
    """Own a lock descriptor deliberately inherited across bootstrap exec."""
    try:
        os.fstat(fd)
    except OSError:
        raise ValueError("Invalid inherited local-development lifecycle lock") from None
    try:
        yield
    finally:
        os.close(fd)


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    name: str
    command: tuple[str, ...]
    environment: dict[str, str] | None = None


class ProcessSupervisor:
    """Start foreground children atomically and drain every owned process group."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.processes: list[tuple[ProcessSpec, subprocess.Popen]] = []
        self.signal: int | None = None
        self.force_stop = False

    def _handle_signal(self, signum: int, _frame: FrameType | None) -> None:
        if self.signal is not None:
            self.force_stop = True
        self.signal = signum

    @contextmanager
    def _signal_handlers(self) -> Iterator[None]:
        previous = {signum: signal.signal(signum, self._handle_signal) for signum in (signal.SIGINT, signal.SIGTERM)}
        try:
            yield
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)

    def run(self, specs: tuple[ProcessSpec, ...]) -> int | None:
        with self._signal_handlers():
            try:
                for spec in specs:
                    process = subprocess.Popen(
                        spec.command,
                        cwd=self.root,
                        env=spec.environment,
                        start_new_session=True,
                    )
                    self.processes.append((spec, process))
                    print(f"Started {spec.name} (pid {process.pid})", flush=True)
                    if self.signal is not None:
                        break
                while self.signal is None:
                    for spec, process in self.processes:
                        if (status := process.poll()) is not None:
                            raise RuntimeError(
                                f"{spec.name} exited (status {status}); stopping development applications"
                            )
                    time.sleep(0.1)
                return self.signal
            finally:
                self.stop()

    def stop(self) -> None:
        for _spec, process in self.processes:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        while True:
            for _spec, process in self.processes:
                process.poll()
            groups = [process.pid for _spec, process in self.processes if _process_group_exists(process.pid)]
            if not groups:
                break
            if self.force_stop:
                for group in groups:
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            time.sleep(0.05)
        for _spec, process in self.processes:
            process.wait()


def supervise(root: Path, specs: tuple[ProcessSpec, ...]) -> int | None:
    return ProcessSupervisor(root).run(specs)


def _process_group_exists(group: int) -> bool:
    try:
        os.killpg(group, 0)
        return True
    except (ProcessLookupError, PermissionError):
        # A retired macOS process group can report EPERM during exit.
        # A group we cannot signal is no longer an owned cleanup target.
        return False
