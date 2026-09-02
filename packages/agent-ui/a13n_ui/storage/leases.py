"""Cross-process liveness leases for local child execution owners."""

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


class ProcessLease:
    """One OS-held exclusive lease identified by a process generation."""

    def __init__(self, path: Path, file: BinaryIO) -> None:
        self._path = path
        self._file: BinaryIO | None = file

    @classmethod
    def acquire(cls, directory: Path, process_generation: str) -> Self:
        """Acquire a new lease or raise when the generation is already live."""

        lease = cls._open(directory, process_generation)
        try:
            _lock(lease._require_file())
        except BaseException:
            lease.close(delete=False)
            raise
        return lease

    @classmethod
    def try_acquire(cls, directory: Path, process_generation: str) -> Self | None:
        """Acquire a generation lease only when no live process holds it."""

        lease = cls._open(directory, process_generation)
        try:
            _lock(lease._require_file())
        except OSError as exc:
            lease.close(delete=False)
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                return None
            raise
        return lease

    @classmethod
    def _open(cls, directory: Path, process_generation: str) -> Self:
        digest = hashlib.sha256(process_generation.encode("utf-8")).hexdigest()
        path = directory / f"{digest}.lease"
        file = path.open("a+b")
        if os.name == "nt" and file.seek(0, os.SEEK_END) == 0:
            file.write(b"\0")
            file.flush()
        return cls(path, file)

    def close(self, *, delete: bool = True) -> None:
        """Release the lease and remove its now-stale marker when possible."""

        file = self._file
        if file is None:
            return
        self._file = None
        try:
            _unlock(file)
        finally:
            file.close()
        if delete:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass

    def _require_file(self) -> BinaryIO:
        file = self._file
        if file is None:
            raise RuntimeError("process lease is closed")
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


__all__ = ["ProcessLease"]
