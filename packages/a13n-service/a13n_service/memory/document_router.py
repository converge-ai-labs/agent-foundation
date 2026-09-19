"""Versioned documents on an explicitly selected retained Environment binding."""

from datetime import datetime
from typing import Annotated

from a13n_harness.document_memory import Document, DocumentChange, DocumentHeading, DocumentInput, DocumentMutation
from a13n_harness.filesystem_memory import DocumentNavigation
from a13n_harness.memory_organization import OrganizationResult
from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from a13n_service.application_errors import ErrorCategory
from a13n_service.etags import resource_etag
from a13n_service.http_types import IfMatch
from a13n_service.iam.http.resource_dependencies import WorkspaceId

from .documents import FileDocuments, StoredScopeCollection
from .router import Actor, _memory
from .service import failure

router = APIRouter(prefix="/api/v1/workspaces/{workspace}/memory-scopes", tags=["memory-documents"])
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=2048)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


class ManagedMemoryDocument(DocumentInput):
    id: str
    version: int
    created_at: datetime
    saved_at: datetime

    @classmethod
    def project(cls, document: Document) -> "ManagedMemoryDocument":
        return cls.model_validate(document.model_dump(include=set(cls.model_fields)))


class ManagedDocumentMutation(BaseModel):
    document: ManagedMemoryDocument
    change_id: str | None

    @classmethod
    def project(cls, mutation: DocumentMutation) -> "ManagedDocumentMutation":
        return cls(document=ManagedMemoryDocument.project(mutation.document), change_id=mutation.change_id)


class FileDocumentCollection(BaseModel):
    items: tuple[DocumentNavigation, ...]
    next_cursor: str | None = None


class ReviseDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change: DocumentChange
    expected_version: int = Field(ge=1)


def documents(request: Request) -> FileDocuments:
    return FileDocuments(_memory(request))


@router.get("")
async def scopes(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    limit: Limit = 50,
    cursor: Cursor = None,
    environment_id: str | None = None,
    subject_id: str | None = None,
    conversation_scope_id: str | None = None,
) -> StoredScopeCollection:
    return await documents(request).scopes(
        actor,
        workspace_id,
        limit=limit,
        cursor=cursor,
        environment_id=environment_id,
        subject_id=subject_id,
        conversation_scope_id=conversation_scope_id,
    )


@router.get("/{scope_id}/documents")
async def listing(
    request: Request, actor: Actor, workspace_id: WorkspaceId, scope_id: str, limit: Limit = 50, cursor: Cursor = None
) -> FileDocumentCollection:
    async with documents(request).open(actor, workspace_id, scope_id) as store:
        items, next_cursor = await store.list_documents(cursor=cursor, limit=limit)
        return FileDocumentCollection(items=items, next_cursor=next_cursor)


@router.post("/{scope_id}/documents", status_code=201)
async def create(
    request: Request, actor: Actor, workspace_id: WorkspaceId, scope_id: str, body: DocumentInput, key: Key
) -> ManagedDocumentMutation:
    async with documents(request).open(actor, workspace_id, scope_id, write=True) as store:
        return ManagedDocumentMutation.project(await store.create_document(body, request_key=f"console:{key}"))


@router.get("/{scope_id}/documents/{document_id}")
async def read(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: WorkspaceId,
    scope_id: str,
    document_id: str,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> ManagedMemoryDocument:
    async with documents(request).open(actor, workspace_id, scope_id) as store:
        document = await store.document(document_id, version=version)
        response.headers["ETag"] = resource_etag(document.id, document.saved_at)
        return ManagedMemoryDocument.project(document)


@router.put("/{scope_id}/documents/{document_id}")
async def revise(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    scope_id: str,
    document_id: str,
    body: ReviseDocument,
    key: Key,
    if_match: IfMatch,
) -> ManagedDocumentMutation:
    async with documents(request).open(actor, workspace_id, scope_id, write=True) as store:
        current = await store.document(document_id, version=body.expected_version)
        if resource_etag(current.id, current.saved_at) != if_match:
            raise failure(
                "version_conflict", "The document changed. Reload before saving.", ErrorCategory.stale_version
            )
        return ManagedDocumentMutation.project(
            await store.revise(
                document_id, expected_version=current.version, change=body.change, request_key=f"console:{key}"
            )
        )


@router.get("/{scope_id}/documents/{document_id}/revisions")
async def history(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    scope_id: str,
    document_id: str,
    before_version: Annotated[int | None, Query(ge=1)] = None,
    limit: Limit = 20,
) -> tuple[ManagedMemoryDocument, ...]:
    async with documents(request).open(actor, workspace_id, scope_id) as store:
        return tuple(
            ManagedMemoryDocument.project(item)
            for item in await store.history(document_id, before_version=before_version, limit=limit)
        )


@router.get("/{scope_id}/documents/{document_id}/toc")
async def toc(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    scope_id: str,
    document_id: str,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> tuple[DocumentHeading, ...]:
    async with documents(request).open(actor, workspace_id, scope_id) as store:
        return await store.toc(document_id, version=version)


@router.delete("/{scope_id}/documents/{document_id}", status_code=204)
async def remove(
    request: Request, actor: Actor, workspace_id: WorkspaceId, scope_id: str, document_id: str
) -> Response:
    async with documents(request).open(actor, workspace_id, scope_id, write=True) as store:
        await store.delete(document_id)
    return Response(status_code=204)


class OrganizationStatus(BaseModel):
    id: str
    run_id: str
    status: str
    error_code: str | None = None
    committed: int = 0
    deferred: int = 0


@router.get("/{scope_id}/organization")
async def organization_status(
    request: Request, actor: Actor, workspace_id: WorkspaceId, scope_id: str
) -> tuple[OrganizationStatus, ...]:
    from sqlalchemy import select

    from a13n_service.storage import short_session

    from .documents import authorize_storage
    from .models import MemoryOrganizationRecord, MemoryStorageRecord

    memory = _memory(request)
    async with short_session(memory.authorizer.sessions) as session:
        storage = await session.get(MemoryStorageRecord, scope_id)
        if storage is None:
            raise failure("memory_scope_not_found", "Memory scope not found.", ErrorCategory.not_found)
        await authorize_storage(session, memory, actor, storage, workspace_id=workspace_id, write=False)
        rows = await session.scalars(
            select(MemoryOrganizationRecord)
            .where(
                MemoryOrganizationRecord.storage_id == scope_id,
            )
            .order_by(MemoryOrganizationRecord.created_at.desc())
            .limit(50)
        )
        return tuple(
            OrganizationStatus(
                id=row.id,
                run_id=row.run_id,
                status=row.status,
                error_code=row.error_code,
                committed=len(OrganizationResult.model_validate(row.result).committed) if row.result else 0,
                deferred=OrganizationResult.model_validate(row.result).deferred if row.result else 0,
            )
            for row in rows
        )
