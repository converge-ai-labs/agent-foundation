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
    """Open one Agent UI data root for this Host process."""

    layout = StorageLayout.from_root(settings.data_root)
    await to_thread.run_sync(layout.prepare)
    process_generation = f"process-{uuid4().hex}"
    async with open_database(layout.database, settings) as database:
        yield LocalStore(
            settings=settings,
            layout=layout,
            database=database,
            objects=ImmutableObjectStore(layout, settings, producer_release=__version__),
            process_generation=process_generation,
        )


__all__ = ["LocalStore", "open_local_store"]
