"""Composed Agent UI local-store lifetime."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from uuid import uuid4

from anyio import to_thread
from pydantic import JsonValue

from a13n_ui import __version__

from .database import Database, open_database
from .layout import StorageLayout
from .leases import ProcessLease
from .objects import ImmutableObjectStore, ObjectEnvelope, ObjectKind, ObjectRef
from .repositories import (
    ChildExecutionRepository,
    ConfigurationRepository,
    EnvironmentStateRepository,
    SessionRepository,
)

if TYPE_CHECKING:
    from a13n_ui.settings import StorageSettings


class LocalStore:
    """Application-owned boundary over SQLite metadata and immutable files."""

    def __init__(
        self,
        *,
        settings: StorageSettings,
        layout: StorageLayout,
        database: Database,
        objects: ImmutableObjectStore,
        process_generation: str,
    ) -> None:
        self.settings = settings
        self.layout = layout
        self.database = database
        self.objects = objects
        self.process_generation = process_generation
        self.configurations = ConfigurationRepository(database.sessions)
        self.sessions = SessionRepository(database.sessions)
        self.child_executions = ChildExecutionRepository(database.sessions)
        self.environment_states = EnvironmentStateRepository(database.sessions)

    async def publish_object(
        self,
        *,
        object_kind: ObjectKind,
        object_schema_version: str,
        payload: JsonValue,
        payload_codec_version: str = "1",
    ) -> ObjectRef:
        """Publish one object file and return its detached reference."""

        envelope = await self.objects.publish(
            object_kind=object_kind,
            object_schema_version=object_schema_version,
            payload=payload,
            payload_codec_version=payload_codec_version,
        )
        return envelope.ref

    async def read_object(self, reference: ObjectRef) -> ObjectEnvelope:
        """Read one exact content-addressed object."""

        return await self.objects.read(reference)

    async def object_count(self) -> int:
        return len(await self.objects.references())


@asynccontextmanager
async def open_local_store(settings: StorageSettings) -> AsyncGenerator[LocalStore]:
    """Open one Agent UI data root and reconcile confirmed-dead child owners."""

    layout = StorageLayout.from_root(settings.data_root)
    await to_thread.run_sync(layout.prepare)
    process_generation = f"process-{uuid4().hex}"
    lease = await to_thread.run_sync(ProcessLease.acquire, layout.leases, process_generation)
    try:
        async with open_database(layout.database, settings) as database:
            store = LocalStore(
                settings=settings,
                layout=layout,
                database=database,
                objects=ImmutableObjectStore(layout, settings, producer_release=__version__),
                process_generation=process_generation,
            )
            await _reconcile_dead_child_owners(store)
            yield store
    finally:
        await to_thread.run_sync(lease.close)


async def _reconcile_dead_child_owners(store: LocalStore) -> None:
    owners = await store.child_executions.running_owner_generations()
    for owner in owners:
        if owner == store.process_generation:
            continue
        lease = await to_thread.run_sync(ProcessLease.try_acquire, store.layout.leases, owner)
        if lease is None:
            continue
        try:
            await store.child_executions.mark_owner_lost(owner_process_generation=owner)
        finally:
            await to_thread.run_sync(lease.close)


__all__ = ["LocalStore", "open_local_store"]
