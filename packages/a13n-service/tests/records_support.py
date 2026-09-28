"""A fake record memory provider for Service tests: namespaced records in memory, and the helpers the record memory
tests share. Its store follows the `RecordStore` rules: IDs are global, and a record of another namespace is
`record_not_found`."""

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx2
from a13n_harness.providers.authentication import Authentication, CredentialMode
from a13n_harness.providers.memory import (
    MemoryProviderDefinition,
    MemoryRecord,
    MemoryStoreError,
    RecordPage,
    RecordStore,
)
from a13n_service.distribution import OSS
from a13n_service.providers.registry import Registry
from pydantic import BaseModel, ConfigDict, SecretStr


class FakeConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    region: str = "local"


class FakeCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    api_key: SecretStr


@dataclass
class Backend:
    """The fake provider's world: each record with its namespace, and every call that reached it."""

    records: dict[str, tuple[str, str]] = field(default_factory=dict)
    # (operation, namespace) of every call.
    calls: list[tuple[str, str]] = field(default_factory=list)
    # Operations that raise this store error code, or this exception, instead of answering.
    failing: dict[str, str | Exception] = field(default_factory=dict)
    # Operations that wait for the event before answering.
    gates: dict[str, asyncio.Event] = field(default_factory=dict)

    def reset(self) -> None:
        self.records.clear()
        self.calls.clear()
        self.failing.clear()
        self.gates.clear()

    def seed(self, namespace: str, *texts: str) -> list[str]:
        ids = [f"rec-{uuid4().hex[:12]}" for _ in texts]
        self.records.update({record_id: (namespace, text) for record_id, text in zip(ids, texts, strict=True)})
        return ids

    def texts(self, namespace: str) -> list[str]:
        return [text for owner, text in self.records.values() if owner == namespace]


BACKEND = Backend()


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


class FakeRecordStore:
    def __init__(self, namespace: str):
        self.namespace = namespace

    async def _enter(self, operation: str) -> None:
        BACKEND.calls.append((operation, self.namespace))
        if (gate := BACKEND.gates.get(operation)) is not None:
            await gate.wait()
        if isinstance(failure := BACKEND.failing.get(operation), Exception):
            raise failure
        if failure is not None:
            raise MemoryStoreError(failure, f"The fake backend failed {operation}.")  # type: ignore[arg-type]

    def _own(self) -> list[MemoryRecord]:
        return [
            MemoryRecord(record_id, text)
            for record_id, (owner, text) in BACKEND.records.items()
            if owner == self.namespace
        ]

    def _require(self, record_id: str) -> None:
        if BACKEND.records.get(record_id, ("", ""))[0] != self.namespace:
            raise MemoryStoreError("record_not_found", f"No record {record_id}.")

    async def search(self, query: str, *, limit: int) -> tuple[MemoryRecord, ...]:
        await self._enter("search")
        words = _words(query)
        scored = [replace(item, score=len(words & _words(item.text)) / max(len(words), 1)) for item in self._own()]
        return tuple(sorted((item for item in scored if item.score), key=lambda item: -(item.score or 0))[:limit])

    async def list(self, *, limit: int, cursor: str | None = None) -> RecordPage:
        await self._enter("list")
        start = int(cursor or 0)
        owned = self._own()
        end = start + limit
        return RecordPage(tuple(owned[start:end]), str(end) if len(owned) > end else None)

    async def add(self, text: str) -> MemoryRecord:
        await self._enter("add")
        [record_id] = BACKEND.seed(self.namespace, text)
        return MemoryRecord(record_id, text)

    async def update(self, record_id: str, text: str) -> MemoryRecord:
        await self._enter("update")
        self._require(record_id)
        BACKEND.records[record_id] = (self.namespace, text)
        return MemoryRecord(record_id, text)

    async def delete(self, record_id: str) -> None:
        await self._enter("delete")
        self._require(record_id)
        del BACKEND.records[record_id]

    async def purge(self) -> None:
        await self._enter("purge")
        for record_id in [item.id for item in self._own()]:
            del BACKEND.records[record_id]


@asynccontextmanager
async def _open(
    configuration: FakeConfiguration, credential: FakeCredential | None, namespace: str, http: httpx2.AsyncClient
) -> AsyncIterator[RecordStore]:
    yield FakeRecordStore(namespace)


FAKE_RECORDS = MemoryProviderDefinition(
    type="fake_records",
    display_name="Fake records",
    configuration_model=FakeConfiguration,
    credential_model=FakeCredential,
    authentication=Authentication(mode=CredentialMode.optional),
    open_store=_open,
)


async def with_fake_records(service: SimpleNamespace) -> dict[str, Any]:
    """Offer the fake record provider in `service` and configure one account of it in the workspace. Deleting a record
    memory then needs the control sweeps paused, or they purge with the registry they started with."""
    service.runtime = replace(service.runtime, registry=Registry.of((*OSS.providers, FAKE_RECORDS)))
    service.app.state.runtime = service.runtime
    BACKEND.reset()
    created = await service.client.post(
        f"{service.api}/memory-providers", json={"type": "fake_records", "name": "Records", "config": {}}
    )
    assert created.status_code == 201, created.text
    return created.json()


async def create_record_memory(
    service: SimpleNamespace, provider: dict[str, Any], name: str = "facts", **fields: Any
) -> dict[str, Any]:
    created = await service.client.post(
        f"{service.api}/memories",
        json={"name": name, "type": "fake_records", "provider_id": provider["id"], **fields},
    )
    assert created.status_code == 201, created.text
    return created.json()
