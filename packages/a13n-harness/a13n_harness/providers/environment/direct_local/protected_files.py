"""Process-local files that native file tools must not open or mutate.

On POSIX, closing any descriptor for an inode releases this process's advisory
locks on that inode. Even a read-only preview can therefore break an embedded
database's locks. Owners register files before opening them and keep the
registration until their database connections have closed.

This is an accidental-access guard for cooperating file APIs, not a sandbox for
shell commands, third-party code, or concurrent filesystem substitutions.
"""

from __future__ import annotations

import errno
from collections.abc import Iterable
from pathlib import Path
from threading import RLock

_lock = RLock()
_protections: set[LocalFileProtection] = set()


class LocalFileProtection:
    """One owner's registration; overlapping registrations close independently."""

    def __init__(self, paths: Iterable[Path]) -> None:
        self._paths = frozenset(path.resolve() for path in paths)
        with _lock:
            _protections.add(self)

    def close(self) -> None:
        """Release only after the owner's native handles have closed."""
        with _lock:
            _protections.discard(self)


def protect_local_files(paths: Iterable[Path]) -> LocalFileProtection:
    """Reserve exact native paths, including files the owner has yet to create."""
    return LocalFileProtection(paths)


def is_protected_local_file(path: Path, *, descendants: bool = False) -> bool:
    """Inspect names and inode aliases without opening any file."""
    with _lock:
        if not _protections:
            return False
        resolved = path.resolve()
        paths = {item for protection in _protections for item in protection._paths}
        if resolved in paths or (descendants and any(item.is_relative_to(resolved) for item in paths)):
            return True
        identity = _identity(resolved)
        # Sidecars may appear after registration. Stat current paths rather than
        # caching their inode numbers or probing their contents.
        return identity is not None and any(identity == _identity(item) for item in paths)


def check_local_file_access(path: Path, *, descendants: bool = False) -> None:
    """Reject before open/close can disturb locks or a mutation can replace data."""
    if is_protected_local_file(path, descendants=descendants):
        raise PermissionError(errno.EACCES, "The file is in use by host storage.")


def _identity(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    return (info.st_dev, info.st_ino) if info.st_ino else None


__all__ = [
    "LocalFileProtection",
    "check_local_file_access",
    "is_protected_local_file",
    "protect_local_files",
]
