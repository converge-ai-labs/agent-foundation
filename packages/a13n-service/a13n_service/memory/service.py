"""Authorized typed memory management without a second memory store."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from a13n_harness.memory import (
    MemoryPaginationUnsupported,
    MemoryRecord,
    MemoryRecordNotFound,
    MemoryWriteUnconfirmed,
    require_memory_subject,
    validate_memory_text,
)
from a13n_harness.memory_plugins import MemoryBackendCatalog

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.collection_cursors import decode_collection_cursor, encode_collection_cursor
from a13n_service.iam import AuthenticatedActor, AuthorizationError
from a13n_service.secrets.crypto import SecretProtector

from .domain import Memory, MemoryAccess, MemoryCollection, MemoryPagination, MemoryScope, MemorySearch
from .execution import open_memory_backend
from .scopes import MemoryAuthorizer

if TYPE_CHECKING:
    from .bots.verification import BotMemoryVerifier


def failure(code: str, message: str, category: ErrorCategory = ErrorCategory.dependency_failure) -> ApplicationError:
    return ApplicationError(code, message, category=category)


@asynccontextmanager
async def memory_io(timeout: float, *, write: bool = False) -> AsyncIterator[None]:
    try:
        async with asyncio.timeout(timeout):
            yield
    except asyncio.CancelledError:
        raise
    except MemoryRecordNotFound as error:
        raise failure("memory_not_found", "Memory not found.", ErrorCategory.not_found) from error
    except MemoryPaginationUnsupported as error:
        raise failure(
            "memory_pagination_unsupported",
            "This memory backend supports bounded lists, not cursor traversal.",
            ErrorCategory.invalid_request,
        ) from error
    except ApplicationError:
        raise
    except Exception as error:
        # Native adapter errors may contain credentials or private memory content.
        if write or isinstance(error, MemoryWriteUnconfirmed):
            raise failure(
                "memory_write_unconfirmed",
                "The memory change could not be confirmed. Check the memory before retrying.",
            ) from error
        raise failure("memory_unavailable", "Memory is temporarily unavailable.", ErrorCategory.unavailable) from error


def project_memory(record: MemoryRecord) -> Memory:
    return Memory(id=record.id, memory=record.text, score=record.score)


class MemoryService:
    def __init__(
        self,
        catalog: MemoryBackendCatalog,
        protector: SecretProtector,
        authorizer: MemoryAuthorizer,
        *,
        timeout: float = 30,
        bot_verifier: "BotMemoryVerifier | None" = None,
    ) -> None:
        self.catalog = catalog
        self.protector = protector
        self.authorizer = authorizer
        self.timeout = timeout
        self.bot_verifier = bot_verifier

    async def access(
        self, *, actor: AuthenticatedActor, workspace_id: str, provider_id: str, selection: MemoryScope
    ) -> MemoryAccess:
        await self.authorizer.authorize(
            actor=actor, workspace_id=workspace_id, provider_id=provider_id, selection=selection
        )
        try:
            await self.authorizer.authorize(
                actor=actor, workspace_id=workspace_id, provider_id=provider_id, selection=selection, write=True
            )
        except AuthorizationError:
            return MemoryAccess(can_write=False)
        return MemoryAccess(can_write=True)

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        provider_id: str,
        selection: MemoryScope,
        limit: int = 1000,
        cursor: str | None = None,
    ) -> MemoryCollection:
        subject, access = await self.authorizer.authorize(
            actor=actor, workspace_id=workspace_id, provider_id=provider_id, selection=selection
        )
        scope = {
            "provider_id": provider_id,
            "subject": {"scope": subject.scope.value, "value": subject.value},
            "limit": limit,
        }
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
        async with memory_io(self.timeout), open_memory_backend(access, self.catalog, self.protector) as backend:
            page = await backend.list(subject, limit=limit, cursor=native_cursor)
            if len(page.items) > limit:
                raise ValueError("Invalid memory page")
            for item in page.items:
                require_memory_subject(item, (subject,))
            pagination = None
            if page.pagination is not None:
                next_cursor = page.pagination.next_cursor
                if next_cursor is not None and (not next_cursor or next_cursor == native_cursor):
                    raise ValueError("Invalid memory continuation")
                pagination = MemoryPagination(
                    next_cursor=encode_collection_cursor({"cursor": next_cursor}, scope=scope, kind="memories")
                    if next_cursor is not None
                    else None
                )
            return MemoryCollection(items=tuple(project_memory(item) for item in page.items), pagination=pagination)

    async def search(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        provider_id: str,
        selection: MemoryScope,
        query: MemorySearch,
    ) -> MemoryCollection:
        subject, access = await self.authorizer.authorize(
            actor=actor, workspace_id=workspace_id, provider_id=provider_id, selection=selection
        )
        async with memory_io(self.timeout), open_memory_backend(access, self.catalog, self.protector) as backend:
            items = await backend.search(query.query, subjects=(subject,), limit=query.limit, threshold=query.threshold)
            if len(items) > query.limit:
                raise ValueError("Invalid memory results")
            for item in items:
                require_memory_subject(item, (subject,))
            return MemoryCollection(items=tuple(project_memory(item) for item in items))

    async def get(
        self, *, actor: AuthenticatedActor, workspace_id: str, provider_id: str, selection: MemoryScope, memory_id: str
    ) -> Memory:
        subject, access = await self.authorizer.authorize(
            actor=actor, workspace_id=workspace_id, provider_id=provider_id, selection=selection
        )
        async with memory_io(self.timeout), open_memory_backend(access, self.catalog, self.protector) as backend:
            record = await backend.get(memory_id, subject=subject)
            require_memory_subject(record, (subject,))
            return project_memory(record)

    async def add(
        self, *, actor: AuthenticatedActor, workspace_id: str, provider_id: str, selection: MemoryScope, text: str
    ) -> Memory:
        validate_memory_text(text)
        subject, access = await self.authorizer.authorize(
            actor=actor, workspace_id=workspace_id, provider_id=provider_id, selection=selection, write=True
        )
        async with (
            memory_io(self.timeout, write=True),
            open_memory_backend(access, self.catalog, self.protector) as backend,
        ):
            record = await backend.add(text, subject=subject)
            require_memory_subject(record, (subject,))
            return project_memory(record)

    async def update(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        provider_id: str,
        selection: MemoryScope,
        memory_id: str,
        text: str,
    ) -> Memory:
        validate_memory_text(text)
        subject, access = await self.authorizer.authorize(
            actor=actor, workspace_id=workspace_id, provider_id=provider_id, selection=selection, write=True
        )
        async with (
            memory_io(self.timeout, write=True),
            open_memory_backend(access, self.catalog, self.protector) as backend,
        ):
            record = await backend.update(memory_id, text, subject=subject)
            require_memory_subject(record, (subject,))
            return project_memory(record)

    async def delete(
        self, *, actor: AuthenticatedActor, workspace_id: str, provider_id: str, selection: MemoryScope, memory_id: str
    ) -> None:
        subject, access = await self.authorizer.authorize(
            actor=actor, workspace_id=workspace_id, provider_id=provider_id, selection=selection, write=True
        )
        async with (
            memory_io(self.timeout, write=True),
            open_memory_backend(access, self.catalog, self.protector) as backend,
        ):
            await backend.delete(memory_id, subject=subject)
