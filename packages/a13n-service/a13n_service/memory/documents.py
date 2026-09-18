"""Authorized management of one retained file corpus; bodies remain in Environment files."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Literal

from a13n_environment.files import FileCommitOperator, FileCommitRequest, FileMutationResult
from a13n_harness.document_memory import MemoryDocumentError
from a13n_harness.filesystem_memory import FilesystemMemoryStore
from a13n_harness.memory import MemoryScope as ScopeKind
from a13n_harness.memory_file_commit import EnvironmentMemoryFileCoordinator
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam import AuthenticatedActor, AuthorizationError
from a13n_service.storage import short_session

from .domain import MemoryScope
from .models import MemoryStorageRecord
from .resources import require_provider
from .scopes import authorize_memory_subject
from .service import MemoryService, failure, memory_io
from .sources import authorize_sources


class StoredMemoryScope(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    scope: str
    subject_id: str
    environment_id: str
    provider_identity: str
    availability: Literal["unresolved"] = "unresolved"
    supports_revisions: bool = True
    supports_changes: bool = False


class StoredScopeCollection(BaseModel):
    items: tuple[StoredMemoryScope, ...]
    next_cursor: str | None = None


async def authorize_storage(
    session: AsyncSession,
    memory: MemoryService,
    actor: AuthenticatedActor,
    storage: MemoryStorageRecord,
    *,
    workspace_id: str,
    write: bool,
) -> None:
    if storage.workspace_id == workspace_id and storage.scope_kind == "conversation":
        if memory.authorize_conversation is None:
            raise AuthorizationError("memory_scope_unavailable", concealed=True)
        await memory.authorize_conversation(session, actor, storage, write)
        await require_provider(
            session,
            organization_id=storage.organization_id,
            workspace_id=workspace_id,
            provider_id=storage.provider_identity,
            eligible=True,
            catalog=memory.catalog,
        )
        return
    if storage.workspace_id != workspace_id or storage.scope_kind not in {"user", "agent", "thread"}:
        raise AuthorizationError("memory_scope_unavailable", concealed=True)
    scope = ScopeKind(storage.scope_kind)
    if scope is ScopeKind.USER and actor.principal.principal_id != storage.subject_id:
        raise AuthorizationError("memory_scope_unavailable", concealed=True)
    subject, organization_id = await authorize_memory_subject(
        session,
        actor=actor,
        workspace_id=workspace_id,
        provider_id=storage.provider_identity,
        selection=MemoryScope(scope=scope, subject_id=None if scope is ScopeKind.USER else storage.subject_id),
        write=write,
    )
    if (subject.value, organization_id) != (storage.subject, storage.organization_id):
        raise AuthorizationError("memory_scope_unavailable", concealed=True)
    if storage.provider_identity != "a13n.filesystem":
        provider = await require_provider(
            session,
            organization_id=organization_id,
            workspace_id=workspace_id,
            provider_id=storage.provider_identity,
            eligible=True,
            catalog=memory.catalog,
        )
        if provider.type != "a13n.filesystem":
            raise AuthorizationError("memory_scope_unavailable", concealed=True)


class FileDocuments:
    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory

    async def scopes(
        self,
        actor: AuthenticatedActor,
        workspace_id: str,
        *,
        environment_id: str | None = None,
        subject_id: str | None = None,
        conversation_scope_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> StoredScopeCollection:
        from a13n_service.collection_cursors import decode_collection_cursor, encode_collection_cursor

        binding = {
            "workspace": workspace_id,
            "environment": environment_id,
            "subject": subject_id,
            "conversation": conversation_scope_id,
            "limit": limit,
        }
        after = ""
        if cursor:
            try:
                after = str(decode_collection_cursor(cursor, scope=binding, kind="file-memory")["id"])
            except (KeyError, ValueError) as error:
                raise failure(
                    "invalid_cursor", "Reload the memory collection.", ErrorCategory.invalid_request
                ) from error
        async with short_session(self.memory.authorizer.sessions) as session:
            query = select(MemoryStorageRecord).where(
                MemoryStorageRecord.workspace_id == workspace_id,
                MemoryStorageRecord.scope_kind.in_(
                    ("conversation",) if conversation_scope_id else ("user", "agent", "thread")
                ),
                MemoryStorageRecord.id > after,
            )
            if conversation_scope_id:
                query = query.where(MemoryStorageRecord.subject_id == conversation_scope_id)
            if environment_id:
                query = query.where(MemoryStorageRecord.environment_id == environment_id)
            if subject_id:
                query = query.where(MemoryStorageRecord.subject_id == subject_id)
            rows = list(await session.scalars(query.order_by(MemoryStorageRecord.id).limit(limit + 1)))
            items = []
            for row in rows[:limit]:
                try:
                    await authorize_storage(session, self.memory, actor, row, workspace_id=workspace_id, write=False)
                except AuthorizationError:
                    continue
                items.append(
                    StoredMemoryScope(
                        id=row.id,
                        scope=row.scope_kind or "",
                        subject_id=row.subject_id or "",
                        environment_id=row.environment_id,
                        provider_identity=row.provider_identity,
                    )
                )
            return StoredScopeCollection(
                items=tuple(items),
                next_cursor=(
                    encode_collection_cursor({"id": rows[limit - 1].id}, scope=binding, kind="file-memory")
                    if len(rows) > limit
                    else None
                ),
            )

    @asynccontextmanager
    async def open(
        self,
        actor: AuthenticatedActor,
        workspace_id: str,
        storage_id: str,
        *,
        write: bool = False,
        operation_authorize: Callable[[], Awaitable[None]] | None = None,
    ) -> AsyncIterator[FilesystemMemoryStore]:
        async def authorize(writing: bool) -> None:
            if operation_authorize is not None:
                await operation_authorize()
            async with short_session(self.memory.authorizer.sessions) as session:
                row = await session.get(MemoryStorageRecord, storage_id)
                if row is None:
                    raise failure("memory_scope_not_found", "Memory scope not found.", ErrorCategory.not_found)
                if row.scope_kind == "conversation" and operation_authorize is not None:
                    if row.workspace_id != workspace_id:
                        raise AuthorizationError("memory_scope_unavailable", concealed=True)
                    await require_provider(
                        session,
                        organization_id=row.organization_id,
                        workspace_id=workspace_id,
                        provider_id=row.provider_identity,
                        eligible=True,
                        catalog=self.memory.catalog,
                    )
                else:
                    await authorize_storage(session, self.memory, actor, row, workspace_id=workspace_id, write=writing)

        await authorize(write)
        async with short_session(self.memory.authorizer.sessions) as session:
            row = await session.get(MemoryStorageRecord, storage_id)
            assert row is not None
            environment_id, backing, root, subject = row.environment_id, row.backing_identity, row.root, row.subject
        access = self.memory.files
        if access is None:
            raise failure("memory_storage_unavailable", "Environment file access is unavailable.")
        async with memory_io(self.memory.timeout, write=write):
            async with access.open(
                actor=actor,
                environment_id=environment_id,
                backing_identity=backing,
                write=write,
                authorize=lambda: authorize(write),
            ) as native:

                class Files:
                    async def read_bytes(self, path: str, *, offset: int = 0, length: int | None = None) -> bytes:
                        return await native.read_bytes(path, offset=offset, length=length)

                    async def list(self, path: str, *, offset: int = 0, max_results: int, include_hidden: bool = False):
                        return await native.list(
                            path, offset=offset, max_results=max_results, include_hidden=include_hidden
                        )

                    async def commit(self, request: FileCommitRequest) -> FileMutationResult:
                        if not write or not isinstance(native, FileCommitOperator):
                            raise MemoryDocumentError("memory_write_unsupported")
                        await authorize(True)
                        return await native.commit(request)

                async def sources(values: tuple[str, ...]) -> None:
                    await authorize(write)
                    async with short_session(self.memory.authorizer.sessions) as session:
                        await authorize_sources(
                            session, actor=actor, storage_id=storage_id, workspace_id=workspace_id, references=values
                        )

                files = Files()
                store = FilesystemMemoryStore(
                    files=files,
                    root=root,
                    scope=subject,
                    store_id=storage_id,
                    principal=actor.principal.principal_id,
                    authorize=authorize,
                    authorize_sources=sources,
                )
                store.coordinator = EnvironmentMemoryFileCoordinator(
                    files, root=store.subject_root, store_id=storage_id, scope=subject
                )
                yield store
                await authorize(write)
