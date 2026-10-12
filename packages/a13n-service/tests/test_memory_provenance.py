"""Provider content stays authoritative; source indexing is best effort and contains no text copy."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from a13n_harness.providers.memory import MemoryRecord
from a13n_service.infra.db import short_session
from a13n_service.resources.memories import provenance
from a13n_service.resources.memories.schemas import MemoryRecordSource
from a13n_service.resources.memories.tables import MemoryRecordSourceRow
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .records_support import BACKEND, create_record_memory, with_fake_records

pytestmark = pytest.mark.anyio


async def test_sources_preserve_creation_detect_external_changes_and_delete(service):
    memory = await create_record_memory(service, await with_fake_records(service))
    collection = f"{service.api}/memories/{memory['id']}/records"
    added = (await service.client.post(collection, json={"text": "likes tea"})).json()
    source = added["provenance"]
    assert source["created"] == source["last_write"]
    assert source["created"]["principal_id"] == service.tenant.principal_id
    assert source["created"]["principal_name"]
    assert source["created"]["run_id"] is None
    assert source["content_matches"] is True
    path = f"{collection}/{added['id']}"
    updated = (await service.client.put(path, json={"text": "likes coffee"})).json()
    assert updated["provenance"]["created"] == source["created"]
    assert updated["provenance"]["last_write"]["confirmed_at"] >= source["last_write"]["confirmed_at"]
    BACKEND.records[added["id"]] = (memory["namespace"], "changed outside Service")
    found = (await service.client.post(collection + "/search", json={"query": "changed"})).json()["items"]
    assert found[0]["provenance"]["content_matches"] is False
    assert (await service.client.delete(path)).status_code == 204
    async with short_session(service.runtime.storage) as session:
        assert await session.scalar(select(MemoryRecordSourceRow)) is None


async def test_adopted_records_have_no_invented_creation_and_memory_deletion_cascades(service):
    memory = await create_record_memory(service, await with_fake_records(service))
    [record_id] = BACKEND.seed(memory["namespace"], "imported")
    collection = f"{service.api}/memories/{memory['id']}/records"
    assert (await service.client.get(collection)).json()["items"][0]["provenance"] is None
    updated = (await service.client.put(f"{collection}/{record_id}", json={"text": "edited"})).json()
    assert updated["provenance"]["created"] is None
    assert updated["provenance"]["last_write"]["principal_id"] == service.tenant.principal_id
    response = await service.client.delete(
        f"{service.api}/memories/{memory['id']}", headers={"if-match": f'"{memory["id"]}:{memory["version"]}"'}
    )
    assert response.status_code == 204
    async with short_session(service.runtime.storage) as session:
        assert await session.scalar(select(MemoryRecordSourceRow)) is None


async def test_late_creation_indexing_does_not_overwrite_a_newer_confirmed_update(service):
    memory = await create_record_memory(service, await with_fake_records(service))
    storage = service.runtime.storage
    at = datetime.now(UTC)
    created = MemoryRecordSource(
        principal_id="creator", run_id=None, attempt_id=None, tool_call_id=None, confirmed_at=at
    )
    changed = created.model_copy(update={"principal_id": "editor", "confirmed_at": at + timedelta(seconds=1)})
    current = MemoryRecord(id="record", text="new")
    await provenance.remember(storage, memory["id"], current, changed, created=False)
    await provenance.remember(storage, memory["id"], MemoryRecord(id="record", text="old"), created, created=True)
    # Concurrent stale and fresh observations must not regress the latest source/digest.
    await asyncio.gather(
        provenance.remember(storage, memory["id"], current, changed, created=False),
        provenance.remember(storage, memory["id"], MemoryRecord(id="record", text="old"), created, created=False),
    )
    [view] = await provenance.views(storage, memory["id"], [current])
    assert view.provenance is not None and view.provenance.content_matches
    assert view.provenance.created == created
    assert view.provenance.last_write == changed


async def test_failed_source_indexing_does_not_retry_or_fail_confirmed_provider_writes(service, monkeypatch):
    memory = await create_record_memory(service, await with_fake_records(service))
    collection = f"{service.api}/memories/{memory['id']}/records"

    @asynccontextmanager
    async def broken(*args, **kwargs):
        raise SQLAlchemyError("unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(provenance, "transaction", broken)
    added = await service.client.post(collection, json={"text": "once"})
    assert added.status_code == 201
    record_id = added.json()["id"]
    assert added.json()["provenance"] is None
    assert (await service.client.put(f"{collection}/{record_id}", json={"text": "twice"})).status_code == 200
    assert (await service.client.delete(f"{collection}/{record_id}")).status_code == 204
    assert [verb for verb, _ in BACKEND.calls] == ["add", "update", "delete"]
    monkeypatch.setattr(provenance, "short_session", broken)
    BACKEND.seed(memory["namespace"], "still readable")
    page = await service.client.get(collection)
    assert page.status_code == 200 and page.json()["items"][0]["text"] == "still readable"
