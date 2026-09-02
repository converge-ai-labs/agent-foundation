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
from .objects import ImmutableObjectStore, ObjectEnvelope, ObjectKind, ObjectRef
from .process_locks import ProcessInstanceLock
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
        app_instance_id: str,
    ) -> None:
        self.settings = settings
        self.layout = layout
        self.database = database
        self.objects = objects
        self.app_instance_id = app_instance_id
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
    app_instance_id = f"app-{uuid4().hex[:16]}"
    process_lock = await to_thread.run_sync(
        ProcessInstanceLock.acquire,
        layout.process_locks,
        app_instance_id,
    )
    try:
        async with open_database(layout.database, settings) as database:
            store = LocalStore(
                settings=settings,
                layout=layout,
                database=database,
                objects=ImmutableObjectStore(layout, settings, producer_release=__version__),
                app_instance_id=app_instance_id,
            )
            await _reconcile_dead_child_owners(store)
            yield store
    finally:
        await to_thread.run_sync(process_lock.close)


async def _reconcile_dead_child_owners(store: LocalStore) -> None:
    owners = await store.child_executions.running_owner_instance_ids()
    for owner in owners:
        if owner == store.app_instance_id:
            continue
        process_lock = await to_thread.run_sync(
            ProcessInstanceLock.try_acquire,
            store.layout.process_locks,
            owner,
        )
        if process_lock is None:
            continue
        try:
            await store.child_executions.mark_owner_lost(owner_app_instance_id=owner)
        finally:
            await to_thread.run_sync(process_lock.close)


__all__ = ["LocalStore", "open_local_store"]
