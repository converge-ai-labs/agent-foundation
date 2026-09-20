"""Account-owned Bot memory APIs; every use case reauthorizes its scope."""

from datetime import date
from typing import Annotated

from a13n_harness.providers.memory.documents import DocumentKind
from fastapi import APIRouter, Header, Query, Request, Response

from a13n_service.memory.router import Actor, _memory

from .domain import (
    ConfigureScope,
    CreateDocument,
    Document,
    DocumentCollection,
    DocumentEntry,
    MemoryIndex,
    Scope,
    ScopeCollection,
    SearchDocuments,
)
from .mutations import create, delete
from .operations import list_operations, operation, reconcile
from .service import BotMemoryService
from .settings import AccountMemorySettings, ReplaceMemorySettings, get_settings, replace_settings

router = APIRouter(prefix="/api/v1/application-accounts/{account_id}", tags=["bot-memory"])
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=2048)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def _service(request: Request) -> BotMemoryService:
    return BotMemoryService(_memory(request))


@router.get("/memory-scopes", response_model=ScopeCollection)
async def scopes(
    request: Request,
    actor: Actor,
    account_id: str,
    provider_id: str,
    limit: Limit = 50,
    cursor: Cursor = None,
    target_id: Annotated[str | None, Query(min_length=1, max_length=72)] = None,
) -> ScopeCollection:
    return await _service(request).scopes(
        actor, account_id, provider_id, target_id=target_id, limit=limit, cursor=cursor
    )


@router.post("/memory-scopes", response_model=Scope)
async def configure(request: Request, actor: Actor, account_id: str, body: ConfigureScope) -> Scope:
    return await _service(request).configure_scope(actor, account_id, body)


@router.get("/memory-scopes/{scope_id}/index", response_model=MemoryIndex)
async def index(request: Request, actor: Actor, account_id: str, scope_id: str, cursor: Cursor = None) -> MemoryIndex:
    return await _service(request).index(actor, account_id, scope_id, cursor=cursor)


@router.get("/memory-scopes/{scope_id}/documents", response_model=DocumentCollection)
async def documents(
    request: Request,
    actor: Actor,
    account_id: str,
    scope_id: str,
    limit: Limit = 50,
    cursor: Cursor = None,
    activity_date: date | None = None,
    kind: DocumentKind | None = None,
    include_shared: bool = True,
) -> DocumentCollection:
    return await _service(request).list(
        actor,
        account_id,
        scope_id,
        limit=limit,
        cursor=cursor,
        activity_date=activity_date,
        kind=kind,
        include_shared=include_shared,
    )


@router.post("/memory-scopes/{scope_id}/documents", response_model=Document, status_code=201)
async def add(
    request: Request, actor: Actor, account_id: str, scope_id: str, body: CreateDocument, key: IdempotencyKey
) -> Document:
    return await create(_service(request), actor, account_id, scope_id, body, key)


@router.post("/memory-scopes/{scope_id}/documents/search", response_model=DocumentCollection)
async def search(
    request: Request, actor: Actor, account_id: str, scope_id: str, body: SearchDocuments
) -> DocumentCollection:
    return await _service(request).search(actor, account_id, scope_id, body)


@router.get("/memory-scopes/{scope_id}/documents/{document_id}", response_model=Document)
async def get(request: Request, actor: Actor, account_id: str, scope_id: str, document_id: str) -> Document:
    return await _service(request).get(actor, account_id, scope_id, document_id)


@router.delete("/memory-scopes/{scope_id}/documents/{document_id}", status_code=204)
async def remove(request: Request, actor: Actor, account_id: str, scope_id: str, document_id: str) -> Response:
    await delete(_service(request), actor, account_id, scope_id, document_id)
    return Response(status_code=204)


@router.get("/memory-scopes/{scope_id}/operations", response_model=DocumentCollection)
async def operations(
    request: Request,
    actor: Actor,
    account_id: str,
    scope_id: str,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> DocumentCollection:
    return await list_operations(_service(request), actor, account_id, scope_id, limit=limit, cursor=cursor)


@router.get("/memory-scopes/{scope_id}/operations/{document_id}", response_model=DocumentEntry)
async def operation_status(
    request: Request,
    actor: Actor,
    account_id: str,
    scope_id: str,
    document_id: str,
) -> DocumentEntry:
    return await operation(_service(request), actor, account_id, scope_id, document_id)


@router.post("/memory-scopes/{scope_id}/operations/{document_id}/reconcile", response_model=DocumentEntry)
async def reconcile_operation(
    request: Request,
    actor: Actor,
    account_id: str,
    scope_id: str,
    document_id: str,
) -> DocumentEntry:
    return await reconcile(_service(request), actor, account_id, scope_id, document_id)


@router.get("/bot/memory-settings", response_model=AccountMemorySettings)
async def memory_settings(request: Request, actor: Actor, account_id: str) -> AccountMemorySettings:
    return await get_settings(_memory(request), actor, account_id)


@router.put("/bot/memory-settings", response_model=AccountMemorySettings)
async def update_memory_settings(
    request: Request, actor: Actor, account_id: str, body: ReplaceMemorySettings
) -> AccountMemorySettings:
    return await replace_settings(_memory(request), actor, account_id, body)
