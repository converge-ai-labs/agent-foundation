"""Human-facing native filesystem operations, never Agent Environment access."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import stat
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from anyio import Lock, to_thread
from pydantic import Field, model_validator

from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.file_context import FileContextSource
from a13n_harness_ui.surfaces import SurfaceModel
from a13n_harness_ui.thread_files import MAX_ATTACHMENT_BYTES, ThreadAttachment

MAX_TEXT_BYTES = 512 * 1024
MAX_DIRECTORY_ENTRIES = 10_000
MAX_DELETE_ENTRIES = 10_000
NativePath = Annotated[str, Field(min_length=1, max_length=4096)]
Revision = Annotated[str, Field(min_length=1, max_length=128)]


class FileEntry(SurfaceModel):
    path: NativePath
    kind: Literal["file", "directory", "symlink", "other"]
    revision: Revision
    size: int
    modified_ns: int
    mode: int
    link_target: str | None = None


class DirectoryPage(SurfaceModel):
    directory: FileEntry
    resolved_path: NativePath
    entries: tuple[FileEntry, ...]
    next_offset: int | None


class FileReadRequest(SurfaceModel):
    path: NativePath
    expected_revision: Revision | None = None


class FileText(SurfaceModel):
    entry: FileEntry
    resolved_path: NativePath
    presentation: Literal["text", "binary", "too_large"]
    text: str | None = None


class FileWriteRequest(SurfaceModel):
    path: NativePath
    expected_revision: Revision | None = None
    text: str = Field(max_length=MAX_TEXT_BYTES)


class DirectoryCreateRequest(SurfaceModel):
    path: NativePath


class FileMoveRequest(SurfaceModel):
    path: NativePath
    destination: NativePath
    expected_revision: Revision


class FileDeleteRequest(SurfaceModel):
    path: NativePath
    expected_revision: Revision
    recursive: bool = False


class FileDeletion(SurfaceModel):
    path: NativePath
    removed_entries: int


class FileCaptureRequest(SurfaceModel):
    path: NativePath
    expected_revision: Revision
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def valid_range(self) -> FileCaptureRequest:
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("Select both start_line and end_line, or neither.")
        if self.start_line is not None and self.end_line is not None and self.end_line < self.start_line:
            raise ValueError("end_line precedes start_line.")
        return self


class FileCapture(SurfaceModel):
    attachment: ThreadAttachment
    prompt_text: str | None


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    entry: FileEntry
    resolved_path: str
    data: bytes


@dataclass(frozen=True, slots=True)
class SelectedFile:
    source: FileContextSource
    data: bytes


def _error(code: str, message: str) -> HarnessUiError:
    return HarnessUiError(message, code=f"host_files_{code}")


def _path(value: str) -> Path:
    if not value or len(value) > 4096 or "\x00" in value:
        raise _error("path_invalid", "Use an absolute native path of at most 4096 characters.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise _error("path_invalid", "The path cannot be represented as UTF-8.") from None
    path = Path(value)
    if not path.is_absolute():
        raise _error("path_invalid", "Host paths must be absolute; Project roots are navigation starts.")
    return path


def _revision(info: os.stat_result) -> str:
    # Windows stat uses creation time for ctime and infers execute bits from the
    # filename; fstat does neither. Compare their common native metadata instead.
    mode = info.st_mode & ~0o111 if os.name == "nt" else info.st_mode
    changed_ns = info.st_birthtime_ns if os.name == "nt" else info.st_ctime_ns
    fields = (info.st_dev, info.st_ino, mode, info.st_nlink, info.st_size, info.st_mtime_ns, changed_ns)
    return hashlib.sha256(repr(fields).encode()).hexdigest()


def _entry(path: Path) -> FileEntry:
    _path(str(path))
    info = path.lstat()
    kind = "other"
    if stat.S_ISLNK(info.st_mode) or path.is_junction():
        kind = "symlink"
    elif stat.S_ISREG(info.st_mode):
        kind = "file"
    elif stat.S_ISDIR(info.st_mode):
        kind = "directory"
    return FileEntry(
        path=str(path),
        kind=kind,
        revision=_revision(info),
        size=info.st_size,
        modified_ns=info.st_mtime_ns,
        mode=stat.S_IMODE(info.st_mode),
        link_target=os.readlink(path) if kind == "symlink" else None,
    )


def _expect(path: Path, revision: str | None) -> FileEntry | None:
    try:
        entry = _entry(path)
    except FileNotFoundError:
        if revision is not None:
            raise _error("conflict", "The observed path no longer exists; refresh before retrying.") from None
        return None
    if entry.revision != revision:
        raise _error("conflict", "The path exists or its revision changed; refresh before retrying.")
    return entry


def _snapshot(request: FileReadRequest, limit: int) -> FileSnapshot:
    path = _path(request.path).resolve(strict=True)
    entry = _entry(path)
    if entry.kind != "file":
        raise _error("type_invalid", "Only regular files support content operations.")
    if request.expected_revision is not None and request.expected_revision != entry.revision:
        raise _error("conflict", "File content changed; refresh before selecting or downloading.")
    if entry.size > limit:
        raise _error("too_large", f"File exceeds the {limit}-byte transfer limit.")
    # Nonblocking open prevents a concurrent FIFO substitution from blocking a worker.
    fd = os.open(path, os.O_RDONLY | (os.O_NONBLOCK | os.O_NOFOLLOW if os.name == "posix" else 0))
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or _revision(before) != entry.revision:
            raise _error("conflict", "File changed while opening it.")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if len(data) > limit:
        raise _error("too_large", f"File exceeds the {limit}-byte transfer limit.")
    if (
        _revision(after) != entry.revision
        or after.st_ctime_ns != before.st_ctime_ns
        or _entry(path).revision != entry.revision
    ):
        raise _error("conflict", "File changed while reading it; no snapshot was returned.")
    return FileSnapshot(entry, str(path), data)


def _rename_no_replace(source: Path, destination: Path) -> None:
    """Use the OS no-clobber operation, never a check followed by POSIX rename."""
    if os.name == "nt":
        os.rename(source, destination)  # Windows rename rejects an existing destination.
        return
    library = ctypes.CDLL(None, use_errno=True)
    try:
        if sys.platform == "linux":
            rename = library.renameat2
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)  # RENAME_NOREPLACE
        elif sys.platform == "darwin":
            rename = library.renamex_np
            rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            result = rename(os.fsencode(source), os.fsencode(destination), 4)  # RENAME_EXCL
        else:
            raise _error("unsupported", "This platform does not support a no-replace native move.")
    except AttributeError:
        raise _error("unsupported", "The native runtime does not provide a no-replace move.") from None
    if result != 0:
        code = ctypes.get_errno()
        if code in {errno.ENOSYS, errno.ENOTSUP, errno.EINVAL}:
            raise _error("unsupported", "This filesystem does not support a no-replace native move.")
        raise OSError(code, os.strerror(code))


class HostFiles:
    """One App's serialized, non-abandoned native I/O; no durable operation store."""

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled
        self._lock = Lock()

    def require_enabled(self) -> None:
        if not self.enabled:
            raise _error("disabled", "Native file access requires --share-computer on this instance.")

    async def _run[T](self, operation: Callable[[], T]) -> T:
        self.require_enabled()
        async with self._lock:
            try:
                return await to_thread.run_sync(operation)
            except PermissionError:
                raise _error("permission_denied", "The server OS account cannot perform this operation.") from None
            except FileNotFoundError:
                raise _error("not_found", "The native path or its parent does not exist.") from None
            except FileExistsError:
                raise _error("conflict", "The destination already exists; no overwrite was requested.") from None
            except OSError as exc:
                code = "cross_device" if exc.errno == errno.EXDEV else "io_error"
                raise _error(
                    code, f"Native filesystem operation failed (errno {exc.errno}); refresh before retrying."
                ) from None

    async def metadata(self, path: str) -> FileEntry:
        return await self._run(lambda: _entry(_path(path)))

    async def browse(
        self, path: str, *, offset: int = 0, limit: int = 200, revision: str | None = None
    ) -> DirectoryPage:
        def read() -> DirectoryPage:
            if not 0 <= offset <= MAX_DIRECTORY_ENTRIES or not 1 <= limit <= 500:
                raise _error("range_invalid", "Directory offset or page size is outside its limit.")
            target = _path(path).resolve(strict=True)
            directory = _entry(target)
            if directory.kind != "directory":
                raise _error("type_invalid", "Browsing requires a directory.")
            if offset and revision is None:
                raise _error("conflict", "Further pages require the observed directory revision.")
            if revision is not None and directory.revision != revision:
                raise _error("conflict", "Directory changed; restart browsing at offset zero.")
            names: list[str] = []
            with os.scandir(target) as iterator:
                for item in iterator:
                    if len(names) == MAX_DIRECTORY_ENTRIES:
                        raise _error("too_large", "Directory exceeds 10000 entries; use a narrower native directory.")
                    names.append(item.name)
            names.sort()
            entries = tuple(_entry(target / name) for name in names[offset : offset + limit])
            if _entry(target).revision != directory.revision:
                raise _error("conflict", "Directory changed during listing; refresh.")
            end = offset + len(entries)
            return DirectoryPage(
                directory=directory,
                resolved_path=str(target),
                entries=entries,
                next_offset=end if end < len(names) else None,
            )

        return await self._run(read)

    async def read_text(self, request: FileReadRequest) -> FileText:
        def read() -> FileText:
            path = _path(request.path).resolve(strict=True)
            entry = _entry(path)
            if request.expected_revision is not None and entry.revision != request.expected_revision:
                raise _error("conflict", "The file revision changed.")
            if entry.kind != "file":
                raise _error("type_invalid", "Only regular files support text reading.")
            if entry.size > MAX_TEXT_BYTES:
                return FileText(entry=entry, resolved_path=str(path), presentation="too_large")
            snapshot = _snapshot(request, MAX_TEXT_BYTES)
            try:
                text = snapshot.data.decode("utf-8")
                if "\x00" in text:
                    raise ValueError("Binary content")
            except ValueError:
                return FileText(entry=snapshot.entry, resolved_path=snapshot.resolved_path, presentation="binary")
            return FileText(entry=snapshot.entry, resolved_path=snapshot.resolved_path, presentation="text", text=text)

        return await self._run(read)

    async def download(self, request: FileReadRequest) -> FileSnapshot:
        return await self._run(lambda: _snapshot(request, MAX_ATTACHMENT_BYTES))

    async def write(self, path: str, data: bytes, *, expected_revision: str | None) -> FileEntry:
        def save() -> FileEntry:
            if len(data) > MAX_ATTACHMENT_BYTES:
                raise _error("too_large", "Uploads cannot exceed 10 MiB.")
            target = _path(path)
            # Pin the resolved parent for this operation, but never implicitly replace a final symlink.
            target = target.parent.resolve(strict=True) / target.name
            current = _expect(target, expected_revision)
            if current is not None and current.kind != "file":
                raise _error(
                    "type_invalid",
                    "Save to a regular file or an absent path; select a symlink's resolved target explicitly.",
                )
            fd, temporary = tempfile.mkstemp(prefix=".a13n-write-", dir=target.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    if current is not None:
                        if os.name == "posix":
                            os.fchmod(stream.fileno(), current.mode)
                        else:
                            os.chmod(temporary, current.mode)
                    os.fsync(stream.fileno())
                _expect(target, expected_revision)
                if current is None:
                    # Atomic no-clobber publication for new files, including uploads.
                    os.link(temporary, target)
                else:
                    os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            # Removing the temporary hard link changes ctime/link count.
            return _entry(target)

        return await self._run(save)

    async def write_text(self, request: FileWriteRequest) -> FileEntry:
        try:
            data = request.text.encode("utf-8")
        except UnicodeEncodeError:
            raise _error("text_invalid", "Text must be valid UTF-8.") from None
        if len(data) > MAX_TEXT_BYTES or b"\x00" in data:
            raise _error(
                "text_invalid", "Editable text must be NUL-free UTF-8 within 512 KiB; use upload for binary data."
            )
        return await self.write(request.path, data, expected_revision=request.expected_revision)

    async def create_directory(self, request: DirectoryCreateRequest) -> FileEntry:
        def create() -> FileEntry:
            path = _path(request.path)
            path.mkdir()
            return _entry(path)

        return await self._run(create)

    async def move(self, request: FileMoveRequest) -> FileEntry:
        def move() -> FileEntry:
            source = _path(request.path)
            destination = _path(request.destination)
            _expect(source, request.expected_revision)
            _rename_no_replace(source, destination)
            return _entry(destination)

        return await self._run(move)

    async def delete(self, request: FileDeleteRequest) -> FileDeletion:
        def delete() -> FileDeletion:
            target = _path(request.path)
            entry = _expect(target, request.expected_revision)
            assert entry is not None
            plan: list[FileEntry] = []
            identities: dict[str, tuple[int, int]] = {}
            link_groups: dict[tuple[int, int], list[str]] = {}
            observed_links: dict[tuple[int, int], str] = {}
            pending = [(entry, 0)]
            while pending:
                item, depth = pending.pop()
                plan.append(item)
                info = Path(item.path).lstat()
                if _revision(info) != item.revision:
                    raise _error("conflict", "Entry changed while preparing deletion.")
                identity = (info.st_dev, info.st_ino)
                identities[item.path] = identity
                if item.kind != "directory" and info.st_nlink > 1:
                    link_groups.setdefault(identity, []).append(item.path)
                    observed_links[identity] = item.revision
                if len(plan) + len(pending) > MAX_DELETE_ENTRIES or depth > 128:
                    raise _error(
                        "too_large", "Deletion exceeds 10000 entries or 128 directory levels; nothing was deleted."
                    )
                if item.kind == "directory" and request.recursive:
                    with os.scandir(item.path) as iterator:
                        for child in iterator:
                            if len(plan) + len(pending) >= MAX_DELETE_ENTRIES:
                                raise _error("too_large", "Deletion exceeds 10000 entries; nothing was deleted.")
                            pending.append((_entry(Path(child.path)), depth + 1))
            removed = 0
            # Check every observed entry before the first side effect. This is not
            # a transaction with external writers; recheck each parent and child below.
            for item in plan:
                _expect(Path(item.path), item.revision)
            try:
                for item in reversed(plan):
                    path = Path(item.path)
                    parent = path.parent
                    parent_fd: int | None = None
                    try:
                        if os.name == "posix":
                            parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                            parent_info = os.fstat(parent_fd)
                        else:
                            parent_info = parent.lstat()
                        expected_parent = identities.get(str(parent))
                        if expected_parent is not None and (parent_info.st_dev, parent_info.st_ino) != expected_parent:
                            raise _error("conflict", "A parent directory was replaced during deletion.")
                        name = path.name if parent_fd is not None else str(path)
                        now = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                        if (now.st_dev, now.st_ino) != identities[item.path]:
                            raise _error("conflict", "An entry was replaced during deletion.")
                        if item.kind == "directory" and request.recursive:
                            if not stat.S_ISDIR(now.st_mode):
                                raise _error("conflict", "A directory was replaced during deletion.")
                        elif _revision(now) != observed_links.get((now.st_dev, now.st_ino), item.revision):
                            raise _error("conflict", "An entry changed during deletion.")
                        if item.kind == "directory" or path.is_junction():
                            os.rmdir(name, dir_fd=parent_fd)
                        else:
                            os.unlink(name, dir_fd=parent_fd)
                        removed += 1
                        links = link_groups.get((now.st_dev, now.st_ino))
                        if links:
                            links.pop()
                            if links:
                                remaining = Path(links[-1]).lstat()
                                before = (
                                    now.st_dev,
                                    now.st_ino,
                                    now.st_mode,
                                    now.st_size,
                                    now.st_mtime_ns,
                                    now.st_nlink - 1,
                                )
                                after = (
                                    remaining.st_dev,
                                    remaining.st_ino,
                                    remaining.st_mode,
                                    remaining.st_size,
                                    remaining.st_mtime_ns,
                                    remaining.st_nlink,
                                )
                                if before != after:
                                    raise _error("conflict", "A hard-linked entry changed during deletion.")
                                # Only this inode's expected unlink-induced ctime/nlink change is accepted.
                                observed_links[(now.st_dev, now.st_ino)] = _revision(remaining)
                    finally:
                        if parent_fd is not None:
                            os.close(parent_fd)
            except (OSError, HarnessUiError) as exc:
                if removed:
                    raise _error(
                        "partial_failure",
                        f"Deletion stopped after removing {removed} entries; refresh before retrying.",
                    ) from exc
                raise
            return FileDeletion(path=str(target), removed_entries=removed)

        return await self._run(delete)

    async def capture(self, request: FileCaptureRequest) -> SelectedFile:
        def capture() -> SelectedFile:
            snapshot = _snapshot(
                FileReadRequest(path=request.path, expected_revision=request.expected_revision), MAX_ATTACHMENT_BYTES
            )
            data = snapshot.data
            if request.start_line is not None and request.end_line is not None:
                if len(data) > MAX_TEXT_BYTES or b"\x00" in data:
                    raise _error("selection_invalid", "Line selection requires editable UTF-8 text within 512 KiB.")
                try:
                    lines = data.decode("utf-8").splitlines(keepends=True)
                except UnicodeDecodeError:
                    raise _error("selection_invalid", "Binary files cannot be selected by line.") from None
                if request.end_line > len(lines):
                    raise _error("selection_invalid", "Selected lines exceed the file.")
                data = "".join(lines[request.start_line - 1 : request.end_line]).encode("utf-8")
            return SelectedFile(
                FileContextSource(
                    path=request.path,
                    resolved_path=snapshot.resolved_path,
                    revision=snapshot.entry.revision,
                    start_line=request.start_line,
                    end_line=request.end_line,
                ),
                data,
            )

        return await self._run(capture)
