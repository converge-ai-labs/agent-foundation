"""A file memory kept as plain files under one local directory."""

from __future__ import annotations

import hashlib
import ntpath
import os
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from anyio import to_thread
from filelock import FileLock

from .contracts import Changes, FileEntry, FileText, FullResync, MemoryStoreError, Origin
from .files import FileFormat, describe, validate_path

_BOOKKEEPING = ".a13n-memory"


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


class DirectoryFileStore:
    """The files under `root`, for local Harness use.

    Versions are content hashes. Every mutation holds an exclusive lock on a file
    under `root/.a13n-memory/`, so compare-and-swap holds across tasks and
    processes, and `move` is one rename under that lock. Symbolic links and
    junctions are never followed. The store keeps no change journal: its cursor is a digest of the
    listing, and any change since a cursor answers a full resync. Attribution is
    ignored.
    """

    def __init__(self, root: str | os.PathLike[str], *, format: FileFormat | None = None) -> None:
        self.root = Path(root)
        self.format = format or FileFormat()

    async def list(self) -> list[FileEntry]:
        return await to_thread.run_sync(self._list)

    async def read(self, path: str) -> FileText:
        path = self._validate(path)
        current = await to_thread.run_sync(self._current, path)
        if current is None:
            raise MemoryStoreError("not_found", f"{path} does not exist.")
        return current

    async def write(
        self,
        path: str,
        text: str,
        *,
        expected: str | None,
        origin: Origin,
        dependencies: Mapping[str, str | None] | None = None,
    ) -> str:
        del origin
        path = self._validate(path)
        describe(text, self.format)
        return await to_thread.run_sync(self._write, path, text, expected, dict(dependencies or {}))

    async def move(
        self,
        source: str,
        destination: str,
        *,
        expected: str,
        origin: Origin,
        dependencies: Mapping[str, str | None] | None = None,
    ) -> str:
        del origin
        source, destination = self._validate(source), self._validate(destination)
        return await to_thread.run_sync(self._move, source, destination, expected, dict(dependencies or {}))

    async def delete(
        self,
        path: str,
        *,
        expected: str,
        origin: Origin,
        dependencies: Mapping[str, str | None] | None = None,
    ) -> None:
        del origin
        path = self._validate(path)
        await to_thread.run_sync(self._delete, path, expected, dict(dependencies or {}))

    async def changes(self, since: str | None) -> Changes | FullResync:
        entries = await self.list()
        digest = hashlib.sha256("".join(f"{entry.path}\0{entry.version}\n" for entry in entries).encode()).hexdigest()
        return Changes(cursor=digest, paths=()) if since == digest else FullResync(cursor=digest)

    async def purge(self) -> None:
        await to_thread.run_sync(self._purge)

    def _purge(self) -> None:
        # Retain the lock inode so writers already waiting on it still serialize.
        with self._locked():
            for child in self.root.iterdir():
                if child.name == _BOOKKEEPING:
                    continue
                if child.is_junction():
                    child.rmdir()
                elif not _is_link(child) and child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()

    def _validate(self, path: str) -> str:
        path = validate_path(path, self.format)
        if path.split("/", 1)[0] == _BOOKKEEPING:
            raise MemoryStoreError("invalid_path", f"{_BOOKKEEPING}/ is reserved for the store's bookkeeping.")
        return path

    def _list(self) -> list[FileEntry]:
        entries: list[FileEntry] = []
        for directory, directories, names in os.walk(self.root):
            parent = Path(directory)
            directories[:] = [
                name
                for name in directories
                if (parent != self.root or name != _BOOKKEEPING) and not _is_link(parent / name)
            ]
            for name in names:
                entry = self._entry(Path(directory, name))
                if entry is not None:
                    entries.append(entry)
        return sorted(entries, key=lambda entry: entry.path)

    def _entry(self, file: Path) -> FileEntry | None:
        """A listed file; files the store could not have written, such as non-UTF-8 ones, are left out."""
        if _is_link(file):
            return None
        try:
            path = validate_path(file.relative_to(self.root).as_posix(), self.format)
            data = file.read_bytes()
            text = data.decode()
        except (MemoryStoreError, OSError, UnicodeDecodeError):
            return None
        try:
            description = describe(text, self.format)
        except MemoryStoreError:
            description = None
        return FileEntry(path=path, version=_version(data), size=len(data), description=description)

    def _locate(self, path: str) -> Path:
        """Resolve a memory-relative path without following links or junctions."""
        if os.name == "nt" and any(":" in part or ntpath.isreserved(part) for part in path.split("/")):
            raise MemoryStoreError("invalid_path", "Memory file paths require ordinary Windows file names.")
        target = self.root
        for part in path.split("/"):
            target /= part
            if _is_link(target):
                raise MemoryStoreError("invalid_path", f"{path} passes through a link.")
        return target

    def _current(self, path: str) -> FileText | None:
        file = self._locate(path)
        try:
            data = file.read_bytes()
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            return None
        except PermissionError:
            # Windows refuses to read a directory as a file with access denied.
            if file.is_dir():
                return None
            raise
        try:
            return FileText(path=path, text=data.decode(), version=_version(data))
        except UnicodeDecodeError as error:
            raise MemoryStoreError("invalid_file", f"{path} is not UTF-8 text.") from error

    def _check(self, path: str, expected: str | None) -> None:
        current = self._current(path)
        if (current.version if current is not None else None) != expected:
            raise MemoryStoreError("version_mismatch", f"{path} changed since it was read.", current=current)

    def _check_dependencies(self, dependencies: Mapping[str, str | None]) -> None:
        for path, version in dependencies.items():
            current = self._current(self._validate(path))
            if (current.version if current is not None else None) != version:
                raise MemoryStoreError(
                    "read_conflict",
                    f"{path} changed since it was observed; reread it and reconsider the write.",
                    current=current,
                )

    def _write(self, path: str, text: str, expected: str | None, dependencies: Mapping[str, str | None]) -> str:
        data = text.encode()
        with self._locked():
            self._check_dependencies(dependencies)
            self._check(path, expected)
            with tempfile.NamedTemporaryFile(dir=self.root / _BOOKKEEPING, delete=False) as staged:
                staged.write(data)
            try:
                target = self._locate(path)
                with _path_conflicts(path, target):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(staged.name, target)
            except BaseException:
                os.unlink(staged.name)
                raise
        return _version(data)

    def _move(self, source: str, destination: str, expected: str, dependencies: Mapping[str, str | None]) -> str:
        with self._locked():
            self._check_dependencies(dependencies)
            self._check(source, expected)
            if (existing := self._current(destination)) is not None:
                raise MemoryStoreError("already_exists", f"{destination} already exists.", current=existing)
            file, target = self._locate(source), self._locate(destination)
            with _path_conflicts(destination, target):
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(file, target)
            self._prune(file.parent)
        return expected

    def _delete(self, path: str, expected: str, dependencies: Mapping[str, str | None]) -> None:
        with self._locked():
            self._check_dependencies(dependencies)
            self._check(path, expected)
            file = self._locate(path)
            file.unlink()
            self._prune(file.parent)

    def _prune(self, directory: Path) -> None:
        """Remove directories the last file left; directories are implicit."""
        while directory != self.root and not any(directory.iterdir()):
            directory.rmdir()
            directory = directory.parent

    @contextmanager
    def _locked(self) -> Iterator[None]:
        bookkeeping = self.root / _BOOKKEEPING
        bookkeeping.mkdir(parents=True, exist_ok=True)
        with FileLock(bookkeeping / "lock"):
            yield


@contextmanager
def _path_conflicts(path: str, target: Path) -> Iterator[None]:
    try:
        yield
    except (FileExistsError, NotADirectoryError, IsADirectoryError, PermissionError) as error:
        # Windows refuses to replace a directory with access denied; any other denial is not a name conflict.
        if isinstance(error, PermissionError) and not target.is_dir():
            raise
        raise MemoryStoreError(
            "invalid_path", f"{path} conflicts with an existing file or directory of the same name."
        ) from error


def _version(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]
