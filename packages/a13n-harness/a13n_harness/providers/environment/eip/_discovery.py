"""Bounded EIP document pages and validated operation-local prefix assembly."""

from __future__ import annotations

import hashlib
import posixpath
from collections.abc import AsyncIterator, Callable
from typing import Never

from a13n_envd_client import EIPSession
from a13n_envd_client.eip import v1 as eip
from a13n_envd_client.errors import EIPMethodError

from a13n_harness.environment.files import (
    FileDocument,
    FileDocumentFailure,
    FileDocumentRequest,
    FileDocumentText,
    FileMetadata,
    FileTextResult,
)
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment.eip._common import convert_error, invoke, new_context, session_client


def _invalid() -> Never:
    raise EnvironmentError("Invalid EIP document discovery response.", code="environment_provider_failure")


def _order(path: str, root: str, basename: str) -> tuple[int, str]:
    root = root.rstrip("/") or "/"
    if path == posixpath.join(root, basename):
        return (0, "")
    parent, name = posixpath.split(path)
    if name != basename or posixpath.dirname(parent) != root or posixpath.basename(parent).startswith("."):
        _invalid()
    return (1, posixpath.basename(parent))


async def discover_documents(
    session: EIPSession,
    request: FileDocumentRequest,
    root: eip.EIPPath,
    metadata: Callable[[eip.FileInfo], FileMetadata],
) -> AsyncIterator[FileDocument]:
    if "file.discover_documents" not in session.descriptor.available_methods:
        raise EnvironmentError("EIP document discovery is not supported.", code="environment_unsupported")
    # Leave space for correlation and the JSON-RPC envelope; never raise a carrier ceiling.
    page_bytes = session.max_response_bytes - 4096
    if page_bytes < 1:
        raise EnvironmentError("Document discovery response budget is too small.", code="environment_too_large")
    max_prefix_bytes = min(
        request.max_bytes or session.descriptor.limits.max_response_bytes,
        session.descriptor.limits.max_response_bytes,
    )
    position: eip.FileDiscoveryPosition | None = None
    pending: eip.FileDocumentText | None = None
    chunks: list[str] = []
    assembled = 0
    previous: tuple[int, str] | None = None
    count = 0
    while True:
        page = await invoke(
            session_client(session).file_discover_documents(
                eip.FileDiscoverDocumentsParams(
                    context=new_context(),
                    root=root,
                    basename=request.basename,
                    max_entries=request.max_entries,
                    line_limit=request.line_limit,
                    max_line_length=request.max_line_length,
                    text_byte_limit=request.max_bytes,
                    result_byte_limit=page_bytes,
                    position=position,
                )
            )
        )
        if len(eip.encode_model(page)) > page_bytes:
            _invalid()
        if page.entries_exceeded:
            raise EnvironmentError(
                "Document discovery exceeds its entry bound.",
                code="environment_too_large",
                details={"reason": "entries_exceeded"},
            )
        if page.omitted_unrepresentable_entries:
            raise EnvironmentError(
                "Document discovery contains unrepresentable paths.",
                code="environment_unsupported",
                details={"reason": "unrepresentable_entries"},
            )
        for item in page.documents:
            if isinstance(item, eip.FileDocumentFailure):
                if pending is not None:
                    _invalid()
                key = _order(item.path.path, root.path, request.basename)
                failure = convert_error(EIPMethodError(item.error))
                if failure.code in {
                    "environment_cancelled",
                    "environment_timeout",
                    "environment_stale_mount",
                    "environment_unavailable",
                }:
                    raise failure
                outcome: FileDocument = FileDocumentFailure(
                    item.path.path,
                    "probe" if item.phase == eip.FileDocumentFailurePhase.PROBE else "read",
                    failure,
                )
            else:
                prefix = item.prefix
                key = _order(prefix.info.path.path, root.path, request.basename)
                data = prefix.text.encode("utf-8")
                if (
                    prefix.info.kind != eip.FileKind.FILE
                    or prefix.line_offset != 0
                    or prefix.lines_read > request.line_limit
                    or any(line < 1 or line > prefix.lines_read for line in prefix.truncated_lines)
                    or item.prefix_byte_length > max_prefix_bytes
                    or item.text_byte_offset != assembled
                    or assembled + len(data) > item.prefix_byte_length
                    or (not data and item.prefix_byte_length != 0)
                ):
                    _invalid()
                if pending is not None and (
                    item.prefix_byte_length != pending.prefix_byte_length
                    or item.prefix_digest != pending.prefix_digest
                    or prefix.model_copy(update={"text": ""}) != pending.prefix.model_copy(update={"text": ""})
                ):
                    raise EnvironmentError("A document changed during prefix assembly.", code="environment_conflict")
                pending = item
                chunks.append(prefix.text)
                assembled += len(data)
                if assembled < item.prefix_byte_length:
                    continue
                text = "".join(chunks)
                if hashlib.sha256(text.encode()).hexdigest() != item.prefix_digest.value:
                    _invalid()
                outcome = FileDocumentText(
                    metadata(prefix.info),
                    FileTextResult(
                        path=prefix.info.path.path,
                        text=text,
                        line_offset=0,
                        lines_read=prefix.lines_read,
                        has_more=prefix.has_more,
                        truncated_lines=prefix.truncated_lines,
                    ),
                )
                pending, chunks, assembled = None, [], 0
            if previous is not None and key <= previous:
                _invalid()
            count += 1
            if count > request.max_entries + 1:
                _invalid()
            previous = key
            yield outcome
        next_position = page.next_position
        if next_position is None:
            if pending is not None:
                _invalid()
            return
        old = (0, 0) if position is None else (position.candidate_offset, position.text_byte_offset)
        new = (next_position.candidate_offset, next_position.text_byte_offset)
        if new <= old or next_position.candidate_offset > request.max_entries or not page.documents:
            _invalid()
        if pending is not None:
            if next_position.text_byte_offset != assembled:
                _invalid()
        elif next_position.text_byte_offset != 0:
            _invalid()
        position = next_position
