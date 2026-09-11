"""App-owned Thread scratch and retained inputs, independent of presentation."""

from __future__ import annotations

import logging
import mimetypes
import os
import re
import shutil
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from anyio import CancelScope, Lock, to_thread
from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict

from a13n_harness_ui.file_context import CapturedSource

logger = logging.getLogger(__name__)
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS = 8
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")


class ThreadAttachment(BaseModel):
    """A Thread-scoped handle; paths are resolved by the App, never by clients."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    attachment_id: str
    name: str
    media_type: str
    size: int
    source: CapturedSource | None = None


@dataclass(frozen=True, slots=True)
class AttachmentUpload:
    name: str
    data: bytes
    media_type: str | None = None
    source: CapturedSource | None = None


@dataclass(frozen=True, slots=True)
class ComposerInput:
    """Expanded authored text plus attachments; no terminal placeholders on the wire."""

    text: str
    attachments: tuple[AttachmentUpload, ...] = ()
    source_id: str | None = None


@contextmanager
def _directory(root: Path, parts: tuple[str, ...], *, create: bool = False) -> Iterator[tuple[Path, int | None]]:
    """Anchor POSIX access to directory descriptors, never re-resolved symlinks.

    Windows only offers Full Control, not the local sandbox security boundary.
    Its fallback still rejects explicit symlinks and directory junctions.
    """
    path = root
    if create:
        root.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    try:
        if os.name == "posix":
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            descriptor = os.open(root, flags)
            for part in parts:
                if create:
                    try:
                        os.mkdir(part, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                child = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
                path /= part
        else:
            for part in ("", *parts):
                path /= part
                if path.is_symlink() or path.is_junction():
                    raise ValueError("Attachment paths must not be symbolic links or junctions.")
                if create:
                    path.mkdir(exist_ok=True)
                if not path.is_dir():
                    raise FileNotFoundError(path)
        yield path, descriptor
    except OSError as exc:
        import errno

        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError("Attachment paths must be directories, not symbolic links.") from exc
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_file(directory: tuple[Path, int | None], name: str, limit: int) -> bytes:
    path, descriptor = directory
    if descriptor is None:
        target = path / name
        if target.is_symlink() or not target.is_file():
            raise ValueError("Attachment files must be regular files, not symbolic links.")
        with target.open("rb") as stream:
            return stream.read(limit)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
    except OSError as exc:
        import errno

        if exc.errno == errno.ELOOP:
            raise ValueError("Attachment files must not be symbolic links.") from exc
        raise
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Attachment files must be regular files.")
        return stream.read(limit)


def _write_file(directory: tuple[Path, int | None], name: str, data: bytes) -> None:
    path, descriptor = directory
    if descriptor is None:
        with (path / name).open("xb") as stream:
            stream.write(data)
    else:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)


class ThreadFiles:
    """Protect touched Threads until App close, including across local processes.

    Hard OS locks protect file use only, not execution ownership. A short gate
    makes registration and pruning atomic. Each App has its own held token, so
    concurrent Apps can use the same Thread without serializing Runs.
    """

    def __init__(self, root: Path, *, retention_seconds: float = 3 * 86400) -> None:
        self.root = root / "threads"
        self._locks = root / "thread-file-locks"
        self._retention = retention_seconds
        self._held: dict[str, FileLock] = {}
        self._mutex = Lock()

    def directory(self, thread_id: str) -> Path:
        if not _SAFE_ID.fullmatch(thread_id):
            raise ValueError("Invalid Thread identifier.")
        return self.root / thread_id

    def _gate(self, thread_id: str) -> FileLock:
        self.directory(thread_id)
        directory = self._locks / thread_id
        directory.mkdir(parents=True, exist_ok=True)
        return FileLock(directory / "gate.lock", timeout=5, thread_local=False)

    def _touch(self, thread_id: str) -> Path:
        directory = self.directory(thread_id)
        with self._gate(thread_id):
            with _directory(directory, ("tmp",), create=True) as (scratch, descriptor):
                os.utime(descriptor if descriptor is not None else scratch, None)
            if thread_id not in self._held:
                token = FileLock(self._locks / thread_id / f"use-{uuid4().hex}.lock", thread_local=False)
                token.acquire()
                self._held[thread_id] = token
        return directory

    async def touch(self, thread_id: str) -> Path:
        async with self._mutex:
            return await to_thread.run_sync(self._touch, thread_id)

    async def stage(self, thread_id: str, upload: AttachmentUpload) -> ThreadAttachment:
        if len(upload.data) > MAX_ATTACHMENT_BYTES:
            raise ValueError("Attachments must not exceed 10 MiB.")
        name = Path(upload.name.replace("\\", "/")).name
        name = "".join(c for c in name if c.isprintable())[:160]
        if name in {"", ".", ".."}:
            raise ValueError("An attachment needs a file name.")
        media_type = upload.media_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        if media_type.startswith("image/"):
            # Shared validation also applies to non-terminal callers.
            from a13n_harness_ui.input_images import image_bytes

            image = await to_thread.run_sync(image_bytes, name, upload.data)
            assert image.media_type is not None
            media_type = image.media_type
        attachment = ThreadAttachment(
            attachment_id=f"attachment-{uuid4().hex}",
            name=name,
            media_type=media_type,
            size=len(upload.data),
            source=upload.source,
        )
        directory = await self.touch(thread_id)
        await to_thread.run_sync(self._stage, directory, attachment, upload.data)
        return attachment

    @staticmethod
    def _stage(directory: Path, attachment: ThreadAttachment, data: bytes) -> None:
        with _directory(directory, ("tmp", "uploads", attachment.attachment_id), create=True) as target:
            _write_file(target, "content", data)
            _write_file(target, "metadata.json", attachment.model_dump_json().encode("utf-8"))

    def _read(self, thread_id: str, attachment_id: str) -> tuple[ThreadAttachment, bytes]:
        if not _SAFE_ID.fullmatch(attachment_id):
            raise ValueError("Invalid attachment identifier.")
        for parent in (("attachments",), ("tmp", "uploads")):
            try:
                with _directory(self.directory(thread_id), (*parent, attachment_id)) as directory:
                    attachment = ThreadAttachment.model_validate_json(_read_file(directory, "metadata.json", 65536))
                    data = _read_file(directory, "content", MAX_ATTACHMENT_BYTES + 1)
                    if (
                        attachment.attachment_id != attachment_id
                        or len(data) != attachment.size
                        or len(data) > MAX_ATTACHMENT_BYTES
                    ):
                        raise ValueError("Attachment content has changed or exceeds the size limit.")
                    return attachment, data
            except FileNotFoundError:
                continue
        raise ValueError("Attachment is missing or its unsubmitted draft has expired.")

    def _read_locked(self, thread_id: str, attachment_id: str) -> tuple[ThreadAttachment, bytes]:
        with self._gate(thread_id):
            return self._read(thread_id, attachment_id)

    async def read(self, thread_id: str, attachment_id: str) -> tuple[ThreadAttachment, bytes]:
        await self.touch(thread_id)
        return await to_thread.run_sync(self._read_locked, thread_id, attachment_id)

    async def retain(self, thread_id: str, attachment_id: str) -> tuple[ThreadAttachment, bytes]:
        await self.touch(thread_id)
        return await to_thread.run_sync(self._retain, thread_id, attachment_id)

    def _retain(self, thread_id: str, attachment_id: str) -> tuple[ThreadAttachment, bytes]:
        with self._gate(thread_id):
            result = self._read(thread_id, attachment_id)
            root = self.directory(thread_id)
            with _directory(root, ("attachments",), create=True) as (destination, destination_fd):
                if destination_fd is None:
                    present = (destination / attachment_id).exists()
                else:
                    try:
                        os.stat(attachment_id, dir_fd=destination_fd, follow_symlinks=False)
                        present = True
                    except FileNotFoundError:
                        present = False
                if not present:
                    with _directory(root, ("tmp", "uploads")) as (source, source_fd):
                        if source_fd is None:
                            (source / attachment_id).rename(destination / attachment_id)
                        else:
                            os.rename(attachment_id, attachment_id, src_dir_fd=source_fd, dst_dir_fd=destination_fd)
            return result

    async def prune(self) -> tuple[str, ...]:
        """Best effort; age selects candidates, held OS locks determine use."""
        async with self._mutex:
            return await to_thread.run_sync(self._prune)

    def _prune(self) -> tuple[str, ...]:
        removed: list[str] = []
        if not self.root.exists():
            return ()
        try:
            directories = tuple(self.root.iterdir())
        except OSError:
            logger.warning("Could not list Thread scratch directories", exc_info=True)
            return ()
        for directory in directories:
            scratch = directory / "tmp"
            if not _SAFE_ID.fullmatch(directory.name) or directory.is_symlink():
                continue
            try:
                with self._gate(directory.name):
                    if scratch.is_symlink() or not scratch.is_dir():
                        continue
                    if time.time() - scratch.stat().st_mtime < self._retention:
                        continue
                    busy = False
                    for path in (self._locks / directory.name).glob("use-*.lock"):
                        token = FileLock(path, timeout=0, thread_local=False)
                        try:
                            with token:
                                pass
                        except Timeout:
                            busy = True
                            break
                        path.unlink(missing_ok=True)
                    if not busy:
                        shutil.rmtree(scratch)
                        removed.append(directory.name)
            except (OSError, Timeout):
                logger.warning("Could not prune Thread scratch: %s", directory.name, exc_info=True)
        return tuple(removed)

    async def close(self) -> None:
        with CancelScope(shield=True):
            async with self._mutex:
                await to_thread.run_sync(self._close)

    def _close(self) -> None:
        for thread_id, token in self._held.items():
            try:
                with self._gate(thread_id):
                    try:
                        with _directory(self.directory(thread_id), ("tmp",)) as (scratch, descriptor):
                            os.utime(descriptor if descriptor is not None else scratch, None)
                    except (FileNotFoundError, ValueError):
                        pass
                    token.release()
                    Path(token.lock_file).unlink(missing_ok=True)
            except (OSError, Timeout):
                logger.warning("Could not update Thread file usage: %s", thread_id, exc_info=True)
            finally:
                token.release()
        self._held.clear()
