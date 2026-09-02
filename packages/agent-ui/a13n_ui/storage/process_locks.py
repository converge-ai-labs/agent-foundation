"""Cross-process liveness locks for local child execution owners."""

from __future__ import annotations

import errno
import hashlib
import os
from pathlib import Path
from typing import BinaryIO, Self

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class ProcessInstanceLock:
    """One OS-held exclusive lock identified by an App instance ID."""

    def __init__(self, path: Path, file: BinaryIO) -> None:
        self._path = path
        self._file: BinaryIO | None = file
        self._locked = False

    @classmethod
    def acquire(cls, directory: Path, app_instance_id: str) -> Self:
        """Acquire a new lock or raise when the App instance is already live."""

        process_lock = cls._open(directory, app_instance_id)
        try:
            _lock(process_lock._require_file())
            process_lock._locked = True
        except BaseException:
            process_lock.close(delete=False)
            raise
        return process_lock

    @classmethod
    def try_acquire(cls, directory: Path, app_instance_id: str) -> Self | None:
        """Acquire an App instance lock only when no live process holds it."""

        process_lock = cls._open(directory, app_instance_id)
        try:
            _lock(process_lock._require_file())
            process_lock._locked = True
        except OSError as exc:
            process_lock.close(delete=False)
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                return None
            raise
        return process_lock

    @classmethod
    def _open(cls, directory: Path, app_instance_id: str) -> Self:
        digest = hashlib.sha256(app_instance_id.encode("utf-8")).hexdigest()
        path = directory / f"{digest}.lock"
        file = path.open("a+b")
        if os.name == "nt" and file.seek(0, os.SEEK_END) == 0:
            file.write(b"\0")
            file.flush()
        return cls(path, file)

    def close(self, *, delete: bool = True) -> None:
        """Release the lock and remove its now-stale marker when possible."""

        file = self._file
        if file is None:
            return
        self._file = None
        try:
            if self._locked:
                _unlock(file)
        finally:
            self._locked = False
            file.close()
        if delete:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass

    def _require_file(self) -> BinaryIO:
        file = self._file
        if file is None:
            raise RuntimeError("process instance lock is closed")
        return file


def _lock(file: BinaryIO) -> None:
    if os.name == "nt":
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(file: BinaryIO) -> None:
    if os.name == "nt":
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


__all__ = ["ProcessInstanceLock"]
