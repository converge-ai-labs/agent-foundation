"""Files the Service places in a run's primary environment, under `/workspace/.a13n`.

Each file is written whole and replaced atomically, so writers of the same bytes converge and a file that exists
is complete.
"""

from collections.abc import AsyncIterator

from a13n_environment.files import FileMetadata, FileOperator
from a13n_environment.models import EnvironmentError

ROOT = "/workspace/.a13n"
_CHUNK_BYTES = 64 * 1024


async def stat(files: FileOperator, path: str) -> FileMetadata | None:
    """The regular file at `path`, or None when there is none."""
    try:
        metadata = await files.stat(path)
    except EnvironmentError as error:
        if error.code == "environment_not_found":
            return None
        raise
    return metadata if metadata.kind == "file" else None


async def write(files: FileOperator, path: str, content: bytes) -> None:
    """Replace the file at `path` with `content`; its directory exists."""
    await files.write_bytes_stream(path, _chunks(content), mode="upsert")


async def _chunks(content: bytes) -> AsyncIterator[bytes]:
    for offset in range(0, len(content), _CHUNK_BYTES):
        yield content[offset : offset + _CHUNK_BYTES]
