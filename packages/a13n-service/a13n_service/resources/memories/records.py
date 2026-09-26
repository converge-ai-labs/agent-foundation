"""A record memory's records through the API, and the store every caller opens over the host transport.

A record memory keeps its records in its Memory Provider's backend, under its namespace. A caller reads the memory
and its provider in a short session, closes it, and then calls the backend through the host's outbound client,
bounded by `providers.operation_seconds` and `providers.response_bytes`. Reads need `read`; adding, updating and
deleting need `run`, as anyone who may run an agent can make it write through the record tools. Writes are
audited by record ID, never by text.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass

import httpx2
from a13n_harness.providers.endpoint_policy import EndpointPolicyError
from a13n_harness.providers.memory import MemoryRecord, MemoryStoreError, RecordStore, validate_record_text
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.audit import record
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, disabled, invalid, not_found
from a13n_service.infra.outbound import open_http
from a13n_service.resources.memories.schemas import (
    MemoryRecordPage,
    MemoryRecordSearch,
    MemoryRecordText,
    MemoryRecordView,
)
from a13n_service.resources.memories.service import require_kind
from a13n_service.resources.memories.tables import MemoryRow
from a13n_service.resources.providers.service import ResolvedProvider, read_provider
from a13n_service.resources.providers.tables import MemoryProviderRow
from a13n_service.resources.rows import find_row
from a13n_service.resources.runtime import Runtime
from a13n_service.settings import MemorySettings
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, Verb, WorkspaceScope

RECORD_KIND = "memory_record"


@dataclass(frozen=True, slots=True)
class RecordMemory:
    """A record memory as a backend call needs it, read in a short session."""

    id: str
    scope: WorkspaceScope
    namespace: str
    provider: ResolvedProvider


async def record_memory(session: AsyncSession, memory: MemoryRow) -> RecordMemory:
    """The record memory with its provider, which must be enabled."""
    require_kind(memory, "record")
    assert memory.provider_id is not None and memory.namespace is not None
    provider = await read_provider(session, MemoryProviderRow, memory.provider_id)
    if not provider.enabled:
        raise disabled(MemoryProviderRow.KIND, provider.id)
    return RecordMemory(
        memory.id, WorkspaceScope(memory.organization_id, memory.workspace_id), memory.namespace, provider
    )


@asynccontextmanager
async def open_record_store(runtime: Runtime, provider: ResolvedProvider, namespace: str) -> AsyncIterator[RecordStore]:
    """The provider's store bound to `namespace`, over the host's outbound client; call it through `backend`."""
    definition = runtime.registry.get("memory", provider.type)
    settings = runtime.settings.providers
    async with (
        open_http(
            runtime.endpoint_policy, timeout=settings.operation_seconds, max_bytes=settings.response_bytes
        ) as client,
        definition.open(
            provider.config, provider.reveal_credential(runtime.keys), namespace=namespace, http=client
        ) as store,
    ):
        yield store


@contextmanager
def backend(*, write: bool = False) -> Iterator[None]:
    """A backend failure the store leaves unclassified: the transport's, the endpoint policy's, or an answer over
    `providers.response_bytes`. A write it interrupts may have happened."""
    try:
        yield
    except (httpx2.HTTPError, EndpointPolicyError, ServiceError):
        if write:
            raise MemoryStoreError("write_unconfirmed", "The memory backend did not confirm the write.") from None
        raise MemoryStoreError("unavailable", "The memory backend is unavailable.") from None


@contextmanager
def refusals(memory: RecordMemory, record_id: str | None = None, *, write: bool = False) -> Iterator[None]:
    """A store refusal as the API reports it."""
    try:
        with backend(write=write):
            yield
    except MemoryStoreError as error:
        message = str(error)
        match error.code:
            case "record_not_found":
                raise not_found(RECORD_KIND, record_id or "") from None
            case "invalid_text":
                raise invalid("text", message) from None
            case "invalid_cursor":
                raise invalid("cursor", message) from None
            case "write_unconfirmed":
                raise conflict(MemoryRow.KIND, memory.id, "write_unconfirmed") from None
            case _:
                dependency = f"memory:{memory.provider.type}"
                raise ServiceError("unavailable", message, {"dependency": dependency}) from None


def _view(item: MemoryRecord) -> MemoryRecordView:
    return MemoryRecordView.model_validate(item)


def _text(body: MemoryRecordText, settings: MemorySettings) -> str:
    try:
        return validate_record_text(body.text, max_chars=settings.record_chars)
    except MemoryStoreError as error:
        raise invalid("text", str(error)) from None


async def _memory(storage: Storage, actor: Principal, workspace_id: str, memory_id: str, verb: Verb) -> RecordMemory:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return await record_memory(session, await find_row(session, actor, MemoryRow, scope, memory_id, verb))


async def _audit(storage: Storage, actor: Principal, memory: RecordMemory, verb: str, record_id: str) -> None:
    async with transaction(storage) as session:
        record(
            session,
            memory.scope,
            actor_id=actor.id,
            action=f"{MemoryRow.KIND}.record.{verb}",
            target_kind=MemoryRow.KIND,
            target_id=memory.id,
            details={"record_id": record_id},
        )


async def list_records(
    runtime: Runtime, actor: Principal, workspace_id: str, memory_id: str, *, limit: int, cursor: str | None
) -> MemoryRecordPage:
    """The records in the provider's order; `cursor` is the provider's own."""
    memory = await _memory(runtime.storage, actor, workspace_id, memory_id, "read")
    async with open_record_store(runtime, memory.provider, memory.namespace) as store:
        with refusals(memory):
            page = await store.list(limit=limit, cursor=cursor)
    return MemoryRecordPage(items=[_view(item) for item in page.records], next_cursor=page.next_cursor)


async def search_records(
    runtime: Runtime, actor: Principal, workspace_id: str, memory_id: str, body: MemoryRecordSearch
) -> MemoryRecordPage:
    """The records most similar to the query, most similar first."""
    memory = await _memory(runtime.storage, actor, workspace_id, memory_id, "read")
    async with open_record_store(runtime, memory.provider, memory.namespace) as store:
        with refusals(memory):
            found = await store.search(body.query, limit=body.limit)
    return MemoryRecordPage(items=[_view(item) for item in found], next_cursor=None)


async def add_record(
    runtime: Runtime, actor: Principal, workspace_id: str, memory_id: str, body: MemoryRecordText
) -> MemoryRecordView:
    text = _text(body, runtime.settings.memory)
    memory = await _memory(runtime.storage, actor, workspace_id, memory_id, "run")
    async with open_record_store(runtime, memory.provider, memory.namespace) as store:
        with refusals(memory, write=True):
            added = await store.add(text)
    await _audit(runtime.storage, actor, memory, "create", added.id)
    return _view(added)


async def update_record(
    runtime: Runtime, actor: Principal, workspace_id: str, memory_id: str, record_id: str, body: MemoryRecordText
) -> MemoryRecordView:
    """Replace the record's text; the last writer wins."""
    text = _text(body, runtime.settings.memory)
    memory = await _memory(runtime.storage, actor, workspace_id, memory_id, "run")
    async with open_record_store(runtime, memory.provider, memory.namespace) as store:
        with refusals(memory, record_id, write=True):
            updated = await store.update(record_id, text)
    await _audit(runtime.storage, actor, memory, "update", updated.id)
    return _view(updated)


async def delete_record(runtime: Runtime, actor: Principal, workspace_id: str, memory_id: str, record_id: str) -> None:
    memory = await _memory(runtime.storage, actor, workspace_id, memory_id, "run")
    async with open_record_store(runtime, memory.provider, memory.namespace) as store:
        with refusals(memory, record_id, write=True):
            await store.delete(record_id)
    await _audit(runtime.storage, actor, memory, "delete", record_id)
