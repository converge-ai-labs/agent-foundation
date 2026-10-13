"""Validated archive values and file-based extraction for local and EIP providers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from io import BytesIO
from typing import IO, TYPE_CHECKING
from zipfile import BadZipFile, ZipFile
from zlib import error as CompressionError

from anyio import to_thread

from a13n_harness.environment._zip import zip_members, zip_path
from a13n_harness.environment.models import EnvironmentError

if TYPE_CHECKING:
    from a13n_harness.environment.files import FileMutationResult, FileOperator


@dataclass(frozen=True, slots=True)
class FileArchive:
    """A ZIP and an exact, bounded manifest of paths below its selected archive root."""

    content: bytes = field(repr=False)
    entries: tuple[tuple[str, int], ...]
    root: str = ""

    def __post_init__(self) -> None:
        for path, size in self.entries:
            zip_path(path)
            if size < 0:
                raise ValueError("archive sizes must be nonnegative")


async def extract_archive_files(files: FileOperator, path: str, archive: FileArchive) -> FileMutationResult:
    """Extract using a provider's bounded file primitives into an absent directory."""
    try:
        with ZipFile(BytesIO(archive.content)) as source:
            selected = zip_members(source, archive.root, archive.entries)
            result = await files.mkdir(path, parents=True, exist_ok=False)
            directories = sorted({name.rpartition("/")[0] for name, _ in selected if "/" in name})
            for directory in directories:
                await files.mkdir(f"{path}/{directory}", parents=True, exist_ok=True)
            for name, item in selected:
                with source.open(item) as reader:
                    await files.write_bytes_stream(f"{path}/{name}", _chunks(reader), mode="create")
            return result
    except (BadZipFile, CompressionError, EOFError, ValueError, KeyError, NotImplementedError) as error:
        raise EnvironmentError("Archive does not match its manifest.", code="environment_request_invalid") from error


async def _chunks(reader: IO[bytes]) -> AsyncIterator[bytes]:
    while chunk := await to_thread.run_sync(reader.read, 65536):
        yield chunk
