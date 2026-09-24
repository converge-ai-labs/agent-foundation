"""A file memory kept as plain files under one local directory."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from anyio import to_thread
from filelock import FileLock

from .contracts import Changes, FileEntry, FileText, FullResync, MemoryStoreError, Origin
from .files import FileFormat, describe, validate_path

_BOOKKEEPING = ".a13n-memory"


class DirectoryFileStore:
    """The files under `root`, for local Harness use.

    Versions are content hashes. Every mutation holds an exclusive lock on a file
    under `root/.a13n-memory/`, so compare-and-swap holds across tasks and
    processes, and `move` is one rename under that lock. Symbolic links are never
    followed. The store keeps no change journal: its cursor is a digest of the
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

    async def write(self, path: str, text: str, *, expected: str | None, origin: Origin) -> str:
        del origin
        path = self._validate(path)
        describe(text, self.format)
        return await to_thread.run_sync(self._write, path, text, expected)

    async def move(self, source: str, destination: str, *, expected: str, origin: Origin) -> str:
        del origin
        source, destination = self._validate(source), self._validate(destination)
        return await to_thread.run_sync(self._move, source, destination, expected)

    async def delete(self, path: str, *, expected: str, origin: Origin) -> None:
        del origin
        path = self._validate(path)
        await to_thread.run_sync(self._delete, path, expected)

    async def changes(self, since: str | None) -> Changes | FullResync:
        entries = await self.list()
        digest = hashlib.sha256("".join(f"{entry.path}\0{entry.version}\n" for entry in entries).encode()).hexdigest()
        return Changes(cursor=digest, paths=()) if since == digest else FullResync(cursor=digest)

    async def purge(self) -> None:
        if self.root.exists():
            await to_thread.run_sync(shutil.rmtree, self.root)

    def _validate(self, path: str) -> str:
        path = validate_path(path, self.format)
        if path.split("/", 1)[0] == _BOOKKEEPING:
            raise MemoryStoreError("invalid_path", f"{_BOOKKEEPING}/ is reserved for the store's bookkeeping.")
        return path

    def _list(self) -> list[FileEntry]:
        entries: list[FileEntry] = []
        for directory, directories, names in os.walk(self.root):
            if Path(directory) == self.root:
                directories[:] = [name for name in directories if name != _BOOKKEEPING]
            for name in names:
                entry = self._entry(Path(directory, name))
                if entry is not None:
                    entries.append(entry)
        return sorted(entries, key=lambda entry: entry.path)

    def _entry(self, file: Path) -> FileEntry | None:
        """A listed file; files the store could not have written, such as non-UTF-8 ones, are left out."""
        if file.is_symlink():
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
        """Where a file lives; a path through a symbolic link names no file of this store."""
        target = self.root / path
        for part in (target, *target.parents):
            if part == self.root:
                break
            if part.is_symlink():
                raise MemoryStoreError("invalid_path", f"{path} passes through a symbolic link.")
        return target

    def _current(self, path: str) -> FileText | None:
        try:
            data = self._locate(path).read_bytes()
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            return None
        try:
            return FileText(path=path, text=data.decode(), version=_version(data))
        except UnicodeDecodeError as error:
            raise MemoryStoreError("invalid_file", f"{path} is not UTF-8 text.") from error

    def _check(self, path: str, expected: str | None) -> None:
        current = self._current(path)
        if (current.version if current is not None else None) != expected:
            raise MemoryStoreError("version_mismatch", f"{path} changed since it was read.", current=current)

    def _write(self, path: str, text: str, expected: str | None) -> str:
        data = text.encode()
        with self._locked():
            self._check(path, expected)
            with tempfile.NamedTemporaryFile(dir=self.root / _BOOKKEEPING, delete=False) as staged:
                staged.write(data)
            try:
                with _path_conflicts(path):
                    target = self._locate(path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(staged.name, target)
            except BaseException:
                os.unlink(staged.name)
                raise
        return _version(data)

    def _move(self, source: str, destination: str, expected: str) -> str:
        with self._locked():
            self._check(source, expected)
            if (existing := self._current(destination)) is not None:
                raise MemoryStoreError("already_exists", f"{destination} already exists.", current=existing)
            file, target = self._locate(source), self._locate(destination)
            with _path_conflicts(destination):
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(file, target)
            self._prune(file.parent)
        return expected

    def _delete(self, path: str, expected: str) -> None:
        with self._locked():
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
def _path_conflicts(path: str) -> Iterator[None]:
    try:
        yield
    except (FileExistsError, NotADirectoryError, IsADirectoryError) as error:
        raise MemoryStoreError(
            "invalid_path", f"{path} conflicts with an existing file or directory of the same name."
        ) from error


def _version(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]
