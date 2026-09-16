"""Explicit approved copies with independently versioned audiences."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a13n_harness.memory import MemoryRecordNotFound
from sqlalchemy import delete, select

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam import AuthenticatedActor, WorkspaceAction
from a13n_service.memory.execution import open_memory_backend
from a13n_service.memory.service import failure, memory_io
from a13n_service.storage import short_session, transaction

from .access import subject
from .audit import audit
from .domain import (
    CreateDocument,
    Document,
    DocumentCollection,
    PublicationAccess,
    PublicationAudience,
    PublishDocument,
)
from .models import DocumentRecord, PublicationRecipientRecord
from .mutations import Publication, create
from .service import require_version
from .sharing import eligible_groups

if TYPE_CHECKING:
    from .service import BotMemoryService


async def publish(
    service: BotMemoryService,
    actor: AuthenticatedActor,
    account_id: str,
    scope_id: str,
    source_id: str,
    body: PublishDocument,
    idempotency_key: str,
) -> Document:
    source = await service.get(actor, account_id, scope_id, source_id)
    if source.shared:
        raise failure(
            "memory_sharing_forbidden", "Only local source documents can be published.", ErrorCategory.forbidden
        )
    return await create(
        service,
        actor,
        account_id,
        scope_id,
        CreateDocument(
            text=body.text,
            title=body.title,
            description=body.description,
            kind=source.kind,
            activity_date=source.activity_date,
        ),
        idempotency_key,
        publication=Publication(source_id=source_id, recipients=body.recipient_scope_ids),
    )


async def audience(
    service: BotMemoryService,
    actor: AuthenticatedActor,
    account_id: str,
    scope_id: str,
    document_id: str,
    body: PublicationAudience,
) -> None:
    async with transaction(service.sessions) as session:
        scope = await service._scope(session, actor, account_id, scope_id, WorkspaceAction.bot_memory_share, lock=True)
        row = await session.scalar(
            select(DocumentRecord)
            .where(
                DocumentRecord.id == document_id,
                DocumentRecord.scope_id == scope_id,
                DocumentRecord.publication_source_id.is_not(None),
            )
            .with_for_update()
        )
        if row is None:
            raise failure("memory_not_found", "Publication not found.", ErrorCategory.not_found)
        require_version(row.version, body.expected_version)
        if row.state != "active":
            raise failure("publication_unavailable", "Publication is not active.", ErrorCategory.conflict)
        source = await session.get(DocumentRecord, row.publication_source_id)
        if source is None or source.state != "active":
            raise failure("publication_unavailable", "Publication source is unavailable.", ErrorCategory.conflict)
        await eligible_groups(session, account_id, scope.provider_id, (scope_id, *body.recipient_scope_ids))
        await session.execute(
            delete(PublicationRecipientRecord).where(PublicationRecipientRecord.document_id == document_id)
        )
        session.add_all(
            PublicationRecipientRecord(document_id=document_id, scope_id=recipient)
            for recipient in body.recipient_scope_ids
        )
        row.version += 1
        await audit(
            session,
            actor,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            action="audience_change",
            resource_id=row.id,
            details={"version": row.version, "recipient_count": len(body.recipient_scope_ids)},
        )


async def list_publications(
    service: BotMemoryService,
    actor: AuthenticatedActor,
    account_id: str,
    scope_id: str,
    *,
    source_id: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> DocumentCollection:
    async with short_session(service.sessions) as session:
        await service._scope(session, actor, account_id, scope_id, WorkspaceAction.bot_memory_share)
        binding = {"scope_id": scope_id, "source_id": source_id, "limit": limit, "collection": "publications"}
        query = select(DocumentRecord).where(
            DocumentRecord.scope_id == scope_id, DocumentRecord.publication_source_id.is_not(None)
        )
        if source_id:
            query = query.where(DocumentRecord.publication_source_id == source_id)
        if cursor:
            query = query.where(DocumentRecord.id > service._cursor(cursor, binding))
        rows = list(await session.scalars(query.order_by(DocumentRecord.id).limit(limit + 1)))
        return DocumentCollection(
            items=tuple(row.to_entry() for row in rows[:limit]), next_cursor=service._next(rows, limit, binding)
        )


async def get_audience(
    service: BotMemoryService,
    actor: AuthenticatedActor,
    account_id: str,
    scope_id: str,
    document_id: str,
) -> PublicationAccess:
    async with short_session(service.sessions) as session:
        await service._scope(session, actor, account_id, scope_id, WorkspaceAction.bot_memory_share)
        row = await session.get(DocumentRecord, document_id)
        if row is None or row.scope_id != scope_id or row.publication_source_id is None:
            raise failure("memory_not_found", "Publication not found.", ErrorCategory.not_found)
        recipients = tuple(
            await session.scalars(
                select(PublicationRecipientRecord.scope_id)
                .where(
                    PublicationRecipientRecord.document_id == document_id,
                )
                .order_by(PublicationRecipientRecord.scope_id)
            )
        )
        return PublicationAccess(version=row.version, recipient_scope_ids=recipients)


async def withdraw(
    service: BotMemoryService,
    actor: AuthenticatedActor,
    account_id: str,
    scope_id: str,
    document_id: str,
    expected_version: int,
) -> None:
    async with transaction(service.sessions) as session:
        scope = await service._scope(session, actor, account_id, scope_id, WorkspaceAction.bot_memory_share, lock=True)
        row = await session.scalar(
            select(DocumentRecord)
            .where(
                DocumentRecord.id == document_id,
                DocumentRecord.scope_id == scope_id,
                DocumentRecord.publication_source_id.is_not(None),
            )
            .with_for_update()
        )
        if row is None:
            raise failure("memory_not_found", "Publication not found.", ErrorCategory.not_found)
        if row.state == "deleted":
            return
        if row.state != "deleting":
            require_version(row.version, expected_version)
            row.state = "deleting"
            row.version += 1
            await audit(
                session,
                actor,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                action="withdraw_requested",
                resource_id=row.id,
                details={"version": row.version},
            )
        native_id = row.native_id
        access, native_subject = await service._provider(session, scope), subject(scope)
    if native_id is None:
        raise failure("memory_write_unconfirmed", "Inspect the native write before completing withdrawal.")
    async with (
        memory_io(service.memory.timeout, write=True),
        open_memory_backend(
            access,
            service.memory.catalog,
            service.memory.protector,
        ) as backend,
    ):
        try:
            await backend.delete(native_id, subject=native_subject)
        except MemoryRecordNotFound:
            pass
    async with transaction(service.sessions) as session:
        await service._scope(session, actor, account_id, scope_id, WorkspaceAction.bot_memory_share, lock=True)
        row = await session.get(DocumentRecord, document_id)
        assert row is not None
        if row.state != "deleted":
            await audit(
                session,
                actor,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                action="withdraw_confirmed",
                resource_id=row.id,
            )
        row.state = "deleted"
