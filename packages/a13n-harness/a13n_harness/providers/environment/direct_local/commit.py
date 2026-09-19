"""POSIX conditional publication using directory descriptors and OS locking."""

from __future__ import annotations

import errno
import hashlib
import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from threading import Event

from ..files import FileCommitRequest
from ..models import EnvironmentError


def _failure(code: str) -> EnvironmentError:
    return EnvironmentError("Conditional file publication could not complete", code=code)


@contextmanager
def _directory(parent: int, parts: tuple[str, ...], *, create: bool = False) -> Iterator[int]:
    current = os.dup(parent)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=current)
                    os.fsync(current)
                except FileExistsError:
                    pass
            opened = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = opened
        yield current
    finally:
        os.close(current)


def _parts(path: str) -> tuple[str, ...]:
    parsed = PurePosixPath(path)
    if not parsed.is_absolute() or str(parsed) != path or ".." in parsed.parts or "\x00" in path:
        raise _failure("environment_request_invalid")
    return parsed.parts[1:]


def publish(root: Path, request: FileCommitRequest, *, max_file_bytes: int, cancelled: Event) -> None:
    if os.name != "posix":
        raise _failure("environment_unsupported")
    import fcntl

    base = _parts(request.root)

    def relative(path: str) -> tuple[str, ...]:
        parts = _parts(path)
        if parts[: len(base)] != base or len(parts) <= len(base):
            raise _failure("environment_denied")
        return parts[len(base) :]

    def check() -> None:
        if cancelled.is_set():
            raise _failure("environment_cancelled")

    conditions = {item.path: item.digest for item in request.conditions}
    count = len(request.conditions) + len(request.directories) + len(request.writes) + len(request.removals)
    if count > 256 or sum(len(item.text.encode()) for item in request.writes) > 2 * 1024 * 1024:
        raise _failure("environment_too_large")
    if len(conditions) != len(request.conditions) or len({item.path for item in request.writes}) != len(request.writes):
        raise _failure("environment_request_invalid")
    for path in (*conditions, *request.directories, *request.removals):
        relative(path)
    for item in request.writes:
        relative(item.path)
        if item.path not in conditions or "\x00" in item.text:
            raise _failure("environment_request_invalid")
        if len(item.text.encode()) > max_file_bytes:
            raise _failure("environment_too_large")
    lock = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    publishing = False
    try:
        while True:
            check()
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                cancelled.wait(0.01)
        with _directory(lock, base, create=True) as corpus:
            total = 0
            for path, expected in conditions.items():
                check()
                parts = relative(path)
                try:
                    with _directory(corpus, parts[:-1]) as parent:
                        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                        with os.fdopen(fd, "rb") as file:
                            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                                raise _failure("environment_denied")
                            raw = file.read(2 * 1024 * 1024 + 1)
                    total += len(raw)
                    if len(raw) > 2 * 1024 * 1024 or total > 8 * 1024 * 1024:
                        raise _failure("environment_too_large")
                    current = hashlib.sha256(raw).hexdigest()
                except FileNotFoundError:
                    current = None
                if current != expected:
                    raise _failure("environment_conflict")
            publishing = True
            for path in request.directories:
                check()
                with _directory(corpus, relative(path), create=True):
                    pass
            for item in request.writes:
                check()
                parts = relative(item.path)
                with _directory(corpus, parts[:-1]) as parent:
                    candidate = ".a13n-commit-" + secrets.token_hex(16)
                    fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                    try:
                        with os.fdopen(fd, "wb") as file:
                            file.write(item.text.encode())
                            file.flush()
                            os.fsync(file.fileno())
                        check()
                        os.replace(candidate, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
                        os.fsync(parent)
                    finally:
                        try:
                            os.unlink(candidate, dir_fd=parent)
                        except FileNotFoundError:
                            pass
            remaining = 10_000

            def remove(parent: int, name: str, depth: int = 0) -> None:
                nonlocal remaining
                check()
                remaining -= 1
                if remaining < 0 or depth > 128:
                    raise _failure("environment_too_large")
                try:
                    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    return
                if stat.S_ISDIR(info.st_mode):
                    with _directory(parent, (name,)) as directory:
                        with os.scandir(directory) as entries:
                            for child in entries:
                                remove(directory, child.name, depth + 1)
                    os.rmdir(name, dir_fd=parent)
                else:
                    os.unlink(name, dir_fd=parent)
                os.fsync(parent)

            for path in request.removals:
                parts = relative(path)
                try:
                    with _directory(corpus, parts[:-1]) as parent:
                        remove(parent, parts[-1])
                except FileNotFoundError:
                    pass
    except (OSError, EnvironmentError) as error:
        if publishing:
            raise _failure("environment_unknown_outcome") from error
        if isinstance(error, EnvironmentError):
            raise
        code = (
            "environment_denied"
            if error.errno in (errno.ELOOP, errno.ENOTDIR, errno.EACCES, errno.EPERM)
            else "environment_unavailable"
        )
        raise _failure(code) from error
    finally:
        os.close(lock)
