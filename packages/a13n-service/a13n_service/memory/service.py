"""Authorized memory management without a second memory store."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from a13n_harness.capabilities.mem0_backends import (
    Mem0OSSBackend,
    Mem0PaginationUnsupported,
    Mem0PlatformBackend,
    Mem0RecordNotFound,
    Mem0Subject,
    added_memory_id,
)

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.collection_cursors import decode_collection_cursor, encode_collection_cursor
from a13n_service.iam import AuthenticatedActor

from .domain import Memory, MemoryCollection, MemoryPagination, MemoryScope, MemorySearch
from .scopes import MemoryAuthorizer

NativeBackend = Mem0OSSBackend | Mem0PlatformBackend


def failure(code: str, message: str, category: ErrorCategory = ErrorCategory.dependency_failure) -> ApplicationError:
    return ApplicationError(code, message, category=category)


@asynccontextmanager
async def memory_io(timeout: float, *, write: bool = False) -> AsyncIterator[None]:
    try:
        async with asyncio.timeout(timeout):
            yield
    except ApplicationError as error:
        if write:
            raise failure(
                "memory_write_unconfirmed",
                "The memory change could not be confirmed. Check the memory before retrying.",
            ) from error
        raise
    except asyncio.CancelledError:
        raise
    except Exception as error:
        # Backend exceptions may include credentials, memory bodies, or remote URLs.
        # A timed-out/cancelled write can have committed; never suggest blind retries.
        if write:
            raise failure(
                "memory_write_unconfirmed",
                "The memory change could not be confirmed. Check the memory before retrying.",
            ) from error
        if isinstance(error, Mem0PaginationUnsupported):
            raise failure(
                "memory_pagination_unsupported",
                "This memory backend supports bounded lists, not cursor traversal.",
                ErrorCategory.invalid_request,
            ) from error
        if isinstance(error, Mem0RecordNotFound):
            raise failure("memory_not_found", "Memory not found.", ErrorCategory.not_found) from error
        raise failure("memory_unavailable", "Memory is temporarily unavailable.", ErrorCategory.unavailable) from error


def project_memory(raw: object) -> Memory:
    if not isinstance(raw, Mapping):
        raise ValueError("Invalid memory response")
    return Memory.model_validate({"id": raw.get("id"), "memory": raw.get("memory"), "score": raw.get("score")})


def require_subject(raw: object, subject: Mem0Subject) -> None:
    if not isinstance(raw, Mapping) or raw.get(subject.field) != subject.value:
        raise failure("memory_not_found", "Memory not found.", ErrorCategory.not_found)


class MemoryService:
    def __init__(self, backend: NativeBackend | None, authorizer: MemoryAuthorizer, *, timeout: float = 30) -> None:
        self.backend = backend
        self.authorizer = authorizer
        self.timeout = timeout

    def require_backend(self) -> NativeBackend:
        if self.backend is None:
            raise failure("memory_unavailable", "Memory is unavailable.", ErrorCategory.unavailable)
        return self.backend

    async def _get(self, backend: NativeBackend, memory_id: str, subject: Mem0Subject) -> Memory:
        raw = await backend.get(memory_id)
        require_subject(raw, subject)
        return project_memory(raw)

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        selection: MemoryScope,
        limit: int = 1000,
        cursor: str | None = None,
    ) -> MemoryCollection:
        subject = await self.authorizer.authorize(actor=actor, workspace_id=workspace_id, selection=selection)
        backend = self.require_backend()
        scope = {"subject": subject.filter(), "limit": limit, "backend": type(backend).__name__}
        native_cursor = None
        if cursor is not None:
            try:
                decoded = decode_collection_cursor(cursor, scope=scope, kind="memories")
                native_cursor = decoded["cursor"]
                if not isinstance(native_cursor, str):
                    raise ValueError("Invalid cursor")
            except (ValueError, KeyError) as error:
                raise failure(
                    "invalid_cursor", "The memory cursor is invalid.", ErrorCategory.invalid_request
                ) from error
        async with memory_io(self.timeout):
            raw = await backend.list(subject, limit=limit, cursor=native_cursor)
            if not isinstance(raw, Mapping) or not isinstance(raw.get("results"), list) or len(raw["results"]) > limit:
                raise ValueError("Invalid memory page")
            for item in raw["results"]:
                require_subject(item, subject)
            next_cursor = raw.get("next_cursor")
            if next_cursor is not None and (not isinstance(next_cursor, str) or next_cursor == native_cursor):
                raise ValueError("Invalid memory continuation")
            pagination = None
            if "next_cursor" in raw:
                pagination = MemoryPagination(
                    next_cursor=encode_collection_cursor({"cursor": next_cursor}, scope=scope, kind="memories")
                    if next_cursor
                    else None,
                )
            return MemoryCollection(items=tuple(project_memory(item) for item in raw["results"]), pagination=pagination)

    async def search(
        self, *, actor: AuthenticatedActor, workspace_id: str, selection: MemoryScope, query: MemorySearch
    ) -> MemoryCollection:
        subject = await self.authorizer.authorize(actor=actor, workspace_id=workspace_id, selection=selection)
        backend = self.require_backend()
        async with memory_io(self.timeout):
            raw = await backend.search(query.query, subjects=(subject,), limit=query.limit, threshold=query.threshold)
            if (
                not isinstance(raw, Mapping)
                or not isinstance(raw.get("results"), list)
                or len(raw["results"]) > query.limit
            ):
                raise ValueError("Invalid memory results")
            for item in raw["results"]:
                require_subject(item, subject)
            return MemoryCollection(items=tuple(project_memory(item) for item in raw["results"]))

    async def get(
        self, *, actor: AuthenticatedActor, workspace_id: str, selection: MemoryScope, memory_id: str
    ) -> Memory:
        subject = await self.authorizer.authorize(actor=actor, workspace_id=workspace_id, selection=selection)
        backend = self.require_backend()
        async with memory_io(self.timeout):
            return await self._get(backend, memory_id, subject)

    async def add(self, *, actor: AuthenticatedActor, workspace_id: str, selection: MemoryScope, text: str) -> Memory:
        subject = await self.authorizer.authorize(
            actor=actor, workspace_id=workspace_id, selection=selection, write=True
        )
        backend = self.require_backend()
        async with memory_io(self.timeout, write=True):
            memory_id = added_memory_id(await backend.add(text, subject=subject))
            result = await self._get(backend, memory_id, subject)
            if result.memory != text:
                raise ValueError("Explicit memory text was not persisted")
            return result

    async def update(
        self, *, actor: AuthenticatedActor, workspace_id: str, selection: MemoryScope, memory_id: str, text: str
    ) -> Memory:
        subject = await self.authorizer.authorize(
            actor=actor, workspace_id=workspace_id, selection=selection, write=True
        )
        backend = self.require_backend()
        deadline = asyncio.get_running_loop().time() + self.timeout
        async with memory_io(self.timeout):
            await self._get(backend, memory_id, subject)
        async with memory_io(max(0, deadline - asyncio.get_running_loop().time()), write=True):
            await backend.update(memory_id, text)
            result = await self._get(backend, memory_id, subject)
            if result.memory != text:
                raise ValueError("Memory update was not confirmed")
            return result

    async def delete(
        self, *, actor: AuthenticatedActor, workspace_id: str, selection: MemoryScope, memory_id: str
    ) -> None:
        subject = await self.authorizer.authorize(
            actor=actor, workspace_id=workspace_id, selection=selection, write=True
        )
        backend = self.require_backend()
        deadline = asyncio.get_running_loop().time() + self.timeout
        async with memory_io(self.timeout):
            await self._get(backend, memory_id, subject)
        async with memory_io(max(0, deadline - asyncio.get_running_loop().time()), write=True):
            await backend.delete(memory_id)
            try:
                await backend.get(memory_id)
            except Mem0RecordNotFound:
                return
            raise ValueError("Memory deletion was not confirmed")
