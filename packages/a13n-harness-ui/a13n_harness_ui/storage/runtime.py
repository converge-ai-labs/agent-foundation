"""Composed Harness UI local-store lifetime."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from a13n_harness import HarnessState
from a13n_logging import get_logger
from anyio import sleep, to_thread
from pydantic import JsonValue
from sqlalchemy import select

from a13n_harness_ui import __version__
from a13n_harness_ui.errors import StoreConflictError

from .bot import BotRepository
from .bot_inbox import BotInboxRepository
from .bot_notes import BotNoteRepository
from .checkpoint_lock import checkpoint_lock
from .checkpoint_pruning import CheckpointPruner
from .compaction import CompactionRepository
from .connectors import ConnectorRepository
from .continuations import read_continuation
from .contracts import ChildExecutionHead, Continuation, StoredChildCheckpoint, StoredThreadInitialState
from .database import Database, open_database, short_session, transaction
from .drafts import DraftRepository
from .envd_sessions import EnvdSessionRepository
from .inspection import InspectionRepository
from .layout import StorageLayout
from .mcp_oauth import McpOAuthRepository
from .models import UsageModelRecord
from .objects import ImmutableObjectStore, ObjectEnvelope, ObjectKind, ObjectRef
from .read_models import project_continuation
from .repositories import (
    ChildExecutionRepository,
    ConfigurationRepository,
    EnvironmentStateRepository,
    ProjectModelPreferenceRepository,
    ThreadRepository,
)
from .tool_executions import ToolExecutionRepository
from .usage import ThreadUsageRepository
from .usage_analytics import UsageAnalyticsRepository
from .voice import VoiceTranscriptRepository
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
        self.checkpoints = CheckpointPruner(database.sessions, objects)
        self._object_count = 0
        self.configurations = ConfigurationRepository(database.sessions)
        self.project_models = ProjectModelPreferenceRepository(database.sessions)
        self.threads = ThreadRepository(database.sessions)
        self.drafts = DraftRepository(database.sessions)
        self.inspections = InspectionRepository(database.sessions)
        self.work = WorkRepository(database.sessions)
        self.usage = ThreadUsageRepository(database.sessions)
        self.compactions = CompactionRepository(database.sessions)
        self.usage_analytics = UsageAnalyticsRepository(database.sessions)
        self.voice = VoiceTranscriptRepository(database.sessions, objects)
        self.bot = BotRepository(database.sessions)
        self.bot_inbox = BotInboxRepository(database.sessions)
        self.bot_notes = BotNoteRepository(database.sessions)
        self.connectors = ConnectorRepository(database.sessions)
        self.mcp_oauth = McpOAuthRepository(database.sessions)
        self.restarts = RestartRepository(database.sessions)
        self.child_executions = ChildExecutionRepository(database.sessions)
        self.environment_states = EnvironmentStateRepository(database.sessions)
        self.envd_sessions = EnvdSessionRepository(database.sessions)
        self.tool_executions = ToolExecutionRepository(database.sessions, objects, layout.root)

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

    async def read_continuation(
        self, thread_id: str, reference: ObjectRef, *, include_display: bool = False
    ) -> Continuation:
        """Load the exact selected head, or report a selection race before file access."""
        async with checkpoint_lock(self.layout.root, thread_id):
            thread = await self.threads.get(thread_id)
            if thread is None or thread.continuation != reference:
                raise StoreConflictError(
                    "The selected continuation changed; refetch before reading.",
                    code="thread_continuation_conflict",
                )
            return await read_continuation(self.objects, reference, include_display=include_display)

    async def read_child_checkpoint(self, head: ChildExecutionHead) -> StoredChildCheckpoint:
        """Coordinate exact selected-child reads with same-Run checkpoint removal."""
        async with checkpoint_lock(self.layout.root, head.child_thread_id):
            current = await self.child_executions.get(head.execution_id)
            if (
                head.selected_checkpoint is None
                or current is None
                or current.selected_checkpoint != head.selected_checkpoint
            ):
                raise StoreConflictError(
                    "The selected child checkpoint changed; refetch.", code="child_checkpoint_conflict"
                )
            return await self.objects.read_model(head.selected_checkpoint, StoredChildCheckpoint)

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

    async def repair_usage_models(self) -> None:
        """Resolve pre-upgrade recipe IDs from immutable Run captures, never current settings."""
        from a13n_harness_ui.composition.models import ResolvedRunComposition
        from a13n_harness_ui.model_runtime import model_recipe_id

        async with short_session(self.database.sessions) as session:
            missing = set(
                await session.scalars(select(UsageModelRecord.recipe_id).where(UsageModelRecord.route.is_(None)))
            )
        if not missing:
            return
        for reference in await self.objects.references(ObjectKind.run_composition):
            composition = await self.objects.read_model(reference, ResolvedRunComposition)
            recipes = list(composition.media_understanding.values())
            for node in composition.agent_nodes():
                recipes.append(node.model)
                recipes.extend(item.model for item in node.capabilities if item.model is not None)
            found = {key: recipe for recipe in recipes if (key := model_recipe_id(recipe)) in missing}
            if found:
                async with transaction(self.database.sessions) as session:
                    await self.usage.index_models(session, found)
                missing.difference_update(found)
                if not missing:
                    return

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
        store = LocalStore(
            settings=settings,
            layout=layout,
            database=database,
            objects=ImmutableObjectStore(layout, settings, producer_release=__version__),
        )
        try:
            yield store
        finally:
            await store.tool_executions.owners.close()
            await store.tool_executions.completion_owners.close()


__all__ = ["LocalStore", "open_local_store"]
