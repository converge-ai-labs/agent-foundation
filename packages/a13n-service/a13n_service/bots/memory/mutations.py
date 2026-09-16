"""Confirmed document creation and deletion with durable visibility fences."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from a13n_harness.memory import MemoryDocumentBackend, MemoryRecordNotFound
from pydantic import JsonValue
from sqlalchemy import select, update

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam import WorkspaceAction
from a13n_service.ids import new_object_id
from a13n_service.memory.execution import open_memory_backend
from a13n_service.memory.service import failure, memory_io
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import utc_now

from .access import Authority, RuntimeAuthority, subject
from .audit import audit
from .domain import CreateDocument, Document, ScopeSettings
from .models import DocumentRecord, PublicationRecipientRecord
from .service import digest
from .sharing import eligible_groups

if TYPE_CHECKING:
    from .service import BotMemoryService


@dataclass(frozen=True, slots=True)
class Publication:
    source_id: str
    recipients: tuple[str, ...]


async def create(
    service: BotMemoryService,
    authority: Authority,
    account_id: str,
    scope_id: str,
    body: CreateDocument,
    idempotency_key: str,
    *,
    publication: Publication | None = None,
) -> Document:
    record_id = ""
    metadata: dict[str, JsonValue] = {}
    access = None
    native_subject = None
    key = digest(idempotency_key)
    fingerprint = digest(body.model_dump_json() + (repr(publication) if publication else ""))
    action = WorkspaceAction.bot_memory_share if publication else WorkspaceAction.bot_memory_create
    async with transaction(service.sessions) as session:
        scope = await service._scope(session, authority, account_id, scope_id, action, lock=True)
        if publication:
            await eligible_groups(session, account_id, scope.provider_id, (scope_id, *publication.recipients))
            source = await session.scalar(
                select(DocumentRecord)
                .where(
                    DocumentRecord.id == publication.source_id,
                    DocumentRecord.scope_id == scope_id,
                    DocumentRecord.state == "active",
                    DocumentRecord.publication_source_id.is_(None),
                )
                .with_for_update()
            )
            if source is None:
                raise failure("memory_not_found", "Publication source not found.", ErrorCategory.not_found)
        existing = await session.scalar(
            select(DocumentRecord).where(DocumentRecord.scope_id == scope_id, DocumentRecord.request_key == key)
        )
        if existing is not None:
            if existing.request_digest != fingerprint:
                raise failure(
                    "idempotency_conflict", "This request key was used for different content.", ErrorCategory.conflict
                )
            if existing.state != "active":
                raise failure(
                    "memory_write_unconfirmed",
                    "Inspect the original operation before repeating it.",
                    ErrorCategory.conflict,
                )
            replay_id = existing.id
        else:
            replay_id = None
        if replay_id is None:
            if body.correction_of:
                source = await session.scalar(
                    select(DocumentRecord)
                    .where(
                        DocumentRecord.id == body.correction_of,
                        DocumentRecord.scope_id == scope_id,
                        DocumentRecord.state == "active",
                        DocumentRecord.publication_source_id.is_(None),
                    )
                    .with_for_update()
                )
                if source is None:
                    raise failure("memory_not_found", "Correction source not found.", ErrorCategory.not_found)
            settings = ScopeSettings.model_validate(scope.settings_json)
            now = utc_now()
            activity = body.activity_date or now.astimezone(ZoneInfo(settings.timezone)).date()
            record_id = new_object_id("mdoc")
            metadata = {
                "record_key": record_id,
                "kind": body.kind,
                "activity_date": activity.isoformat(),
                "timezone": settings.timezone,
                "title": body.title,
                "description": body.description,
                "a13n_scope": "conversation",
            }
            if isinstance(authority, RuntimeAuthority):
                metadata["source_run_id"] = authority.context().run_id
                metadata["source_agent_id"] = authority.agent_id
            if body.correction_of:
                metadata["correction_of"] = body.correction_of
            if publication:
                metadata["publication_source_id"] = publication.source_id
            row = DocumentRecord(
                id=record_id,
                scope_id=scope_id,
                state="pending",
                request_key=key,
                request_digest=fingerprint,
                body_digest=digest(body.text),
                title=body.title,
                description=body.description,
                kind=body.kind,
                activity_date=activity,
                timezone=settings.timezone,
                metadata_json=dict(metadata),
                correction_of=body.correction_of,
                publication_source_id=publication.source_id if publication else None,
                created_at=now,
                version=1,
            )
            session.add(row)
            await session.flush()
            if publication:
                session.add_all(
                    PublicationRecipientRecord(document_id=record_id, scope_id=recipient)
                    for recipient in publication.recipients
                )
            access, native_subject = await service._provider(session, scope), subject(scope)
    if replay_id is not None:
        return await service.get(authority, account_id, scope_id, replay_id, publication=publication is not None)
    assert access is not None and native_subject is not None and record_id
    try:
        async with (
            memory_io(service.memory.timeout, write=True),
            open_memory_backend(access, service.memory.catalog, service.memory.protector) as backend,
        ):
            if not isinstance(backend, MemoryDocumentBackend):
                raise failure(
                    "memory_documents_unsupported", "Provider does not support document writes.", ErrorCategory.conflict
                )
            record = await backend.add_document(body.text, subject=native_subject, metadata=metadata)
        async with transaction(service.sessions) as session:
            # Persist the exact locator even if authority changes before activation.
            await session.execute(
                update(DocumentRecord).where(DocumentRecord.id == record_id).values(native_id=record.id)
            )
        async with transaction(service.sessions) as session:
            scope = await service._scope(session, authority, account_id, scope_id, action, lock=True)
            if publication:
                await eligible_groups(session, account_id, scope.provider_id, (scope_id, *publication.recipients))
                source = await session.scalar(
                    select(DocumentRecord).where(DocumentRecord.id == publication.source_id).with_for_update()
                )
                if source is None or source.state != "active":
                    raise failure(
                        "memory_write_unconfirmed", "Publication source is no longer available.", ErrorCategory.conflict
                    )
            row = await session.scalar(select(DocumentRecord).where(DocumentRecord.id == record_id).with_for_update())
            assert row is not None
            row.native_id = record.id
            if row.state != "pending":
                raise failure(
                    "memory_write_unconfirmed", "Document visibility changed during creation.", ErrorCategory.conflict
                )
            row.saved_at, row.state = utc_now(), "active"
            await audit(
                session,
                authority,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                action="publish" if publication else "create",
                resource_id=row.id,
                details={"scope_id": scope.id},
            )
            return Document(**row.to_entry().model_dump(), text=record.text)
    except BaseException:
        # Record uncertainty even on cancellation; cleanup owns a short, bounded transaction.
        async def mark() -> None:
            async with transaction(service.sessions) as session:
                await session.execute(
                    update(DocumentRecord)
                    .where(DocumentRecord.id == record_id, DocumentRecord.state == "pending")
                    .values(state="unconfirmed")
                )

        task = asyncio.create_task(mark())
        try:
            await asyncio.wait_for(asyncio.shield(task), 2)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        raise


async def delete(
    service: BotMemoryService, authority: Authority, account_id: str, scope_id: str, document_id: str
) -> None:
    async with transaction(service.sessions) as session:
        scope = await service._scope(
            session, authority, account_id, scope_id, WorkspaceAction.bot_memory_delete, lock=True
        )
        row = await session.scalar(
            select(DocumentRecord)
            .where(
                DocumentRecord.id == document_id,
                DocumentRecord.scope_id == scope_id,
                DocumentRecord.publication_source_id.is_(None),
            )
            .with_for_update()
        )
        if row is None:
            raise failure("memory_not_found", "Memory not found.", ErrorCategory.not_found)
        if row.state == "deleted":
            return
        if row.state != "deleting":
            await audit(
                session,
                authority,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                action="delete_requested",
                resource_id=row.id,
                details={"scope_id": scope.id},
            )
        row.state = "deleting"
        await session.execute(
            update(DocumentRecord)
            .where(DocumentRecord.publication_source_id == document_id, DocumentRecord.state != "deleted")
            .values(state="deleting")
        )
    # The source fence prevents every publication activation before external cleanup.
    while True:
        async with short_session(service.sessions) as session:
            scope = await service._scope(session, authority, account_id, scope_id, WorkspaceAction.bot_memory_delete)
            rows = list(
                await session.scalars(
                    select(DocumentRecord)
                    .where(DocumentRecord.publication_source_id == document_id, DocumentRecord.state == "deleting")
                    .limit(50)
                )
            )
            candidates = [(row.id, row.native_id) for row in rows]
            if not candidates:
                row = await session.get(DocumentRecord, document_id)
                assert row is not None
                candidates = [(row.id, row.native_id)]
            access, native_subject = await service._provider(session, scope), subject(scope)
        for candidate_id, native_id in candidates:
            if native_id is None:
                raise failure(
                    "memory_write_unconfirmed", "A document write requires reconciliation before deletion can complete."
                )
            async with (
                memory_io(service.memory.timeout, write=True),
                open_memory_backend(access, service.memory.catalog, service.memory.protector) as backend,
            ):
                try:
                    await backend.delete(native_id, subject=native_subject)
                except MemoryRecordNotFound:
                    pass
            async with transaction(service.sessions) as session:
                scope = await service._scope(
                    session, authority, account_id, scope_id, WorkspaceAction.bot_memory_delete
                )
                await session.execute(
                    update(DocumentRecord)
                    .where(DocumentRecord.id == candidate_id, DocumentRecord.state == "deleting")
                    .values(state="deleted")
                )
                await audit(
                    session,
                    authority,
                    organization_id=scope.organization_id,
                    workspace_id=scope.workspace_id,
                    action="delete_confirmed",
                    resource_id=candidate_id,
                    details={"scope_id": scope.id},
                )
            if candidate_id == document_id:
                return
