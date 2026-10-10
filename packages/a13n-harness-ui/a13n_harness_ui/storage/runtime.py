"""Composed Harness UI local-store lifetime."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from a13n_harness import HarnessState
from a13n_logging import get_logger
from anyio import sleep, to_thread
from pydantic import JsonValue

from a13n_harness_ui import __version__
from a13n_harness_ui.errors import StoreConflictError

from .checkpoint_lock import checkpoint_lock
from .contracts import StoredContinuation, StoredThreadInitialState
from .database import Database, open_database
from .inspection import InspectionRepository
from .layout import StorageLayout
from .objects import ImmutableObjectStore, ObjectEnvelope, ObjectKind, ObjectRef
from .read_models import project_continuation
from .repositories import (
    ChildExecutionRepository,
    ConfigurationRepository,
    EnvironmentStateRepository,
    ProjectModelPreferenceRepository,
    ThreadRepository,
)
from .usage import ThreadUsageRepository
from .work import WorkRepository

if TYPE_CHECKING:
    from a13n_harness_ui.settings import StorageSettings


class LocalStore:
    """Application-owned boundary over SQLite metadata and immutable files."""

    def __init__(
        self,
        *,
        settings: StorageSettings,
        layout: StorageLayout,
        database: Database,
        objects: ImmutableObjectStore,
    ) -> None:
        from .restarts import RestartRepository

        self.settings = settings
        self.layout = layout
        self.database = database
        self.objects = objects
        self._object_count = 0
        self.configurations = ConfigurationRepository(database.sessions)
        self.project_models = ProjectModelPreferenceRepository(database.sessions)
        self.threads = ThreadRepository(database.sessions)
        self.inspections = InspectionRepository(database.sessions)
        self.work = WorkRepository(database.sessions)
        self.usage = ThreadUsageRepository(database.sessions)
        self.restarts = RestartRepository(database.sessions)
        self.child_executions = ChildExecutionRepository(database.sessions)
        self.environment_states = EnvironmentStateRepository(database.sessions)

    async def publish_object(
        self,
        *,
        object_kind: ObjectKind,
        object_schema_version: str,
        payload: JsonValue,
        payload_codec_version: str | None = None,
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

    async def read_continuation(self, thread_id: str, reference: ObjectRef) -> StoredContinuation:
        """Load the exact selected head, or report a selection race before file access."""
        async with checkpoint_lock(self.layout.root, thread_id):
            thread = await self.threads.get(thread_id)
            if thread is None or thread.continuation != reference:
                raise StoreConflictError(
                    "The selected continuation changed; refetch before reading.",
                    code="thread_continuation_conflict",
                )
            return await self.objects.read_model(reference, StoredContinuation)

    async def publish_work(self, thread_id: str, reference: ObjectRef, state: HarnessState) -> bool:
        """Best-effort derived publication cannot invalidate a selected checkpoint."""
        from a13n_harness_ui.thread_work import build_work_projection

        try:
            if state.thread_id != thread_id:
                raise ValueError("Work state belongs to another Thread")
            data = await to_thread.run_sync(build_work_projection, state)
            return await self.work.publish(thread_id, reference, data)
        except Exception as exc:
            get_logger("a13n_harness_ui.storage").warning(
                "Could not publish Thread work projection",
                extra={"thread_id": thread_id, "error_type": type(exc).__name__},
            )
            return False

    async def repair_read_models(self) -> tuple[str, ...]:
        """Bounded, resumable maintenance; no object reads on query paths."""
        repaired: list[str] = []
        work_attempts: set[tuple[str, ObjectRef]] = set()
        after = ""
        while batch := await self.threads.missing_read_models(after=after):
            for thread_id, reference in batch:
                work_attempts.add((thread_id, reference))
                value = None
                try:
                    value = await self.read_continuation(thread_id, reference)
                    projection = await to_thread.run_sync(project_continuation, value)
                    if await self.threads.repair_read_model(thread_id, reference, projection):
                        repaired.append(thread_id)
                    await self.publish_work(thread_id, reference, value.harness_state)
                    del value
                except Exception as exc:
                    # Decoder diagnostics are already sanitized. Never include
                    # chained validation errors or persisted payloads in logs.
                    get_logger("a13n_harness_ui.storage").warning(
                        "Could not rebuild Thread read model",
                        extra={"thread_id": thread_id, "error_type": type(exc).__name__},
                    )
                after = thread_id
                await sleep(0)
        after = ""
        while batch := await self.work.missing(after=after):
            for thread_id, reference in batch:
                after = thread_id
                if (thread_id, reference) in work_attempts:
                    continue
                value = None
                try:
                    value = (
                        await self.read_continuation(thread_id, reference)
                        if reference.object_kind is ObjectKind.continuation
                        else await self.objects.read_model(reference, StoredThreadInitialState)
                    )
                    if await self.publish_work(thread_id, reference, value.harness_state):
                        repaired.append(thread_id)
                    del value
                except Exception as exc:
                    get_logger("a13n_harness_ui.storage").warning(
                        "Could not rebuild Thread work projection",
                        extra={"thread_id": thread_id, "error_type": type(exc).__name__},
                    )
                after = thread_id
                await sleep(0)
        return tuple(dict.fromkeys(repaired))

    async def refresh_object_count(self) -> None:
        self._object_count = len(await self.objects.references())

    async def object_count(self) -> int:
        """Return the maintenance sample without enumerating files on status requests."""
        return self._object_count


@asynccontextmanager
async def open_local_store(settings: StorageSettings) -> AsyncGenerator[LocalStore]:
    """Open one verified Harness UI data root."""

    layout = StorageLayout.from_root(settings.data_root)
    await to_thread.run_sync(layout.prepare)
    async with open_database(layout.database, settings) as database:
        yield LocalStore(
            settings=settings,
            layout=layout,
            database=database,
            objects=ImmutableObjectStore(layout, settings, producer_release=__version__),
        )


__all__ = ["LocalStore", "open_local_store"]
