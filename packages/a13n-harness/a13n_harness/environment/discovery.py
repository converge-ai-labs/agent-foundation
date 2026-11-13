"""Explicit document discovery over existing bounded file operations."""

from __future__ import annotations

import posixpath
from collections.abc import AsyncIterator, Awaitable, Callable

from a13n_harness.environment._ordered import map_ordered
from a13n_harness.environment._streams import closing_stream
from a13n_harness.environment.files import (
    FileDocument,
    FileDocumentFailure,
    FileDocumentRequest,
    FileDocumentText,
    FileEntriesResult,
    FileOperator,
)
from a13n_harness.environment.models import EnvironmentError

_DOCUMENT_SCOPE_FAILURES = frozenset(
    {"environment_cancelled", "environment_timeout", "environment_stale_mount", "environment_unavailable"}
)


def document_failure(path: str, phase: str, error: EnvironmentError) -> FileDocumentFailure:
    if error.code in _DOCUMENT_SCOPE_FAILURES:
        raise error
    return FileDocumentFailure(path, "probe" if phase == "probe" else "read", error)


async def discover_documents(
    files: FileOperator,
    request: FileDocumentRequest,
    *,
    read_document: Callable[[str], Awaitable[FileDocument | None]] | None = None,
    entries: FileEntriesResult | None = None,
) -> AsyncIterator[FileDocument]:
    """Compatibility implementation selected explicitly by non-native providers."""
    if entries is None:
        entries = await files.list(request.root, max_results=request.max_entries, include_hidden=False)
    if entries.has_more:
        raise EnvironmentError(
            "Document discovery exceeds its entry bound.",
            code="environment_too_large",
            details={"reason": "entries_exceeded"},
        )
    try:
        for entry in entries.entries:
            entry.path.encode("utf-8")
    except UnicodeEncodeError as error:
        raise EnvironmentError(
            "Document discovery contains unrepresentable paths.",
            code="environment_unsupported",
            details={"reason": "unrepresentable_entries"},
        ) from error
    # Listings may canonicalize a selected directory link. Keep child reads in
    # the requested namespace so discovery preserves source and mount routing.
    candidates = [
        posixpath.join(request.root, request.basename),
        *(
            posixpath.join(request.root, posixpath.basename(item.path), request.basename)
            for item in sorted(entries.entries, key=lambda item: item.path)
            if item.kind == "directory"
        ),
    ]

    async def read(path: str) -> FileDocument | None:
        return await read_document_prefix(files, path, request)

    async with closing_stream(map_ordered(candidates, read_document or read)) as stream:
        async for result in stream:
            if isinstance(result, Exception):
                raise result
            if result is not None:
                yield result


async def read_document_prefix(files: FileOperator, path: str, request: FileDocumentRequest) -> FileDocument | None:
    try:
        info = await files.stat(path)
    except EnvironmentError as error:
        if error.code in {"environment_not_found", "environment_unsupported"}:
            return None
        return document_failure(path, "probe", error)
    if info.kind not in {"file", "symlink"}:
        return None
    try:
        prefix = await files.read_text(
            path, line_limit=request.line_limit, max_line_length=request.max_line_length, max_bytes=request.max_bytes
        )
    except EnvironmentError as error:
        return document_failure(path, "read", error)
    # A regular-file read proves the target kind, not a symlink's target size.
    if info.kind == "symlink":
        info = info.model_copy(update={"kind": "file", "size": None})
    return FileDocumentText(info, prefix)
