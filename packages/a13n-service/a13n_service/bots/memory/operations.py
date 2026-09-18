"""Inspect and reconcile uncertain writes without repeating a Provider add."""

from datetime import timedelta

from a13n_harness.providers.memory.contracts import MemoryDocumentBackend, require_memory_subject
from sqlalchemy import select

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam import AuthenticatedActor, WorkspaceAction
from a13n_service.memory.execution import open_memory_backend
from a13n_service.memory.service import failure, memory_io
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import assume_utc, utc_now

from .access import subject
from .audit import audit
from .domain import DocumentCollection, DocumentEntry
from .models import DocumentRecord
from .service import BotMemoryService, digest


async def list_operations(
    service: BotMemoryService,
    actor: AuthenticatedActor,
    account_id: str,
    scope_id: str,
    *,
    limit: int = 50,
    cursor: str | None = None,
) -> DocumentCollection:
    if not 1 <= limit <= 100:
        raise failure("invalid_limit", "Select 1 to 100 operations.", ErrorCategory.invalid_request)
    async with short_session(service.sessions) as session:
        await service._scope(session, actor, account_id, scope_id, WorkspaceAction.bot_memory_share)
        binding = {"account_id": account_id, "scope_id": scope_id, "limit": limit, "collection": "operations"}
        query = select(DocumentRecord).where(
            DocumentRecord.scope_id == scope_id,
            DocumentRecord.state.in_(("pending", "unconfirmed", "deleting")),
        )
        if cursor:
            query = query.where(DocumentRecord.id > service._cursor(cursor, binding))
        rows = list(await session.scalars(query.order_by(DocumentRecord.id).limit(limit + 1)))
        return DocumentCollection(
            items=tuple(row.to_entry() for row in rows[:limit]), next_cursor=service._next(rows, limit, binding)
        )


async def operation(
    service: BotMemoryService,
    actor: AuthenticatedActor,
    account_id: str,
    scope_id: str,
    document_id: str,
) -> DocumentEntry:
    async with short_session(service.sessions) as session:
        await service._scope(session, actor, account_id, scope_id, WorkspaceAction.bot_memory_share)
        row = await session.get(DocumentRecord, document_id)
        if row is None or row.scope_id != scope_id:
            raise failure("memory_not_found", "Memory operation not found.", ErrorCategory.not_found)
        return row.to_entry()


async def reconcile(
    service: BotMemoryService,
    actor: AuthenticatedActor,
    account_id: str,
    scope_id: str,
    document_id: str,
) -> DocumentEntry:
    async with transaction(service.sessions) as session:
        scope = await service._scope(session, actor, account_id, scope_id, WorkspaceAction.bot_memory_share, lock=True)
        row = await session.scalar(
            select(DocumentRecord)
            .where(
                DocumentRecord.id == document_id,
                DocumentRecord.scope_id == scope_id,
            )
            .with_for_update()
        )
        if row is None:
            raise failure("memory_not_found", "Memory operation not found.", ErrorCategory.not_found)
        if row.state in {"active", "deleted"}:
            return row.to_entry()
        if row.state == "pending":
            if assume_utc(row.created_at) + timedelta(seconds=service.memory.timeout + 60) > utc_now():
                raise failure(
                    "memory_operation_running", "The original write may still be running.", ErrorCategory.conflict
                )
            # Fence a late original activation before inspecting its possible external effect.
            row.state = "unconfirmed"
        native_id, metadata, expected_digest, title = row.native_id, dict(row.metadata_json), row.body_digest, row.title
        access, native_subject = await service._provider(session, scope), subject(scope)
    async with (
        memory_io(service.memory.timeout),
        open_memory_backend(
            access,
            service.memory.catalog,
            service.memory.protector,
        ) as backend,
    ):
        if native_id is None:
            if not isinstance(backend, MemoryDocumentBackend):
                raise failure(
                    "memory_documents_unsupported", "Provider cannot inspect document writes.", ErrorCategory.conflict
                )
            candidates = await backend.search_documents(
                title, subject=native_subject, record_keys=(document_id,), limit=2
            )
            if not candidates:
                # Search is not complete enumeration and cannot establish absence or rollback.
                return await operation(service, actor, account_id, scope_id, document_id)
            if len(candidates) != 1:
                raise failure("memory_write_unconfirmed", "Multiple native documents require inspection.")
            native_id = candidates[0].id
        record = await backend.get(native_id, subject=native_subject)
        require_memory_subject(record, (native_subject,))
        if digest(record.text) != expected_digest or dict(record.metadata) != metadata:
            raise failure("memory_write_unconfirmed", "The native document does not match the reserved write.")
    async with transaction(service.sessions) as session:
        scope = await service._scope(session, actor, account_id, scope_id, WorkspaceAction.bot_memory_share, lock=True)
        row = await session.scalar(select(DocumentRecord).where(DocumentRecord.id == document_id).with_for_update())
        assert row is not None
        if row.state in {"active", "deleted"}:
            return row.to_entry()
        if row.native_id is not None and row.native_id != native_id:
            raise failure("memory_write_unconfirmed", "Conflicting native document identity requires inspection.")
        row.native_id = native_id
        if row.state == "unconfirmed":
            if row.publication_source_id is not None:
                # Retired publication writes can be cleaned up, never reactivated.
                row.state = "deleting"
                return row.to_entry()
            row.state = "active"
            row.saved_at = utc_now()
            row.version += 1
            await audit(
                session,
                actor,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                action="reconcile_confirmed",
                resource_id=row.id,
                details={"state": row.state},
            )
        return row.to_entry()
