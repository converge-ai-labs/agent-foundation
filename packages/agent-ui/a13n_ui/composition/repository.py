"""Durable indexes for immutable Agent and Environment snapshots."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, cast

from anyio import Lock
from pydantic import ValidationError
from sqlalchemy import select

from a13n_ui.errors import CompositionError, StoreIntegrityError
from a13n_ui.storage.database import short_session, transaction
from a13n_ui.storage.models import CompositionSnapshotRecord
from a13n_ui.storage.objects import ObjectKind, ObjectRef
from a13n_ui.storage.runtime import LocalStore

from .models import (
    ResolvedAgentSnapshot,
    ResolvedEnvironmentSnapshot,
    SnapshotReference,
)

type Snapshot = ResolvedAgentSnapshot | ResolvedEnvironmentSnapshot


class SnapshotRepository:
    """Publish snapshots before selecting durable detached references."""

    def __init__(self, store: LocalStore) -> None:
        self._store = store
        self._publish_lock = Lock()

    async def publish_agent(self, snapshot: ResolvedAgentSnapshot) -> SnapshotReference:
        return await self._publish("agent", snapshot)

    async def publish_environment(
        self,
        snapshot: ResolvedEnvironmentSnapshot,
    ) -> SnapshotReference:
        return await self._publish("environment", snapshot)

    async def agent(self, reference: SnapshotReference) -> ResolvedAgentSnapshot:
        if reference.snapshot_kind != "agent":
            raise ValueError("Agent snapshot lookup requires an Agent reference")
        snapshot = await self._read(reference)
        if not isinstance(snapshot, ResolvedAgentSnapshot):
            raise StoreIntegrityError(
                "The selected Agent snapshot has the wrong payload type.",
                code="agent_snapshot_invalid",
            )
        return snapshot

    async def environment(
        self,
        reference: SnapshotReference,
    ) -> ResolvedEnvironmentSnapshot:
        if reference.snapshot_kind != "environment":
            raise ValueError("Environment snapshot lookup requires an Environment reference")
        snapshot = await self._read(reference)
        if not isinstance(snapshot, ResolvedEnvironmentSnapshot):
            raise StoreIntegrityError(
                "The selected Environment snapshot has the wrong payload type.",
                code="environment_snapshot_invalid",
            )
        return snapshot

    async def find(
        self,
        *,
        snapshot_kind: Literal["agent", "environment"],
        logical_digest: str,
    ) -> SnapshotReference | None:
        async with short_session(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as session:
            row = (
                await session.execute(
                    select(CompositionSnapshotRecord).where(
                        CompositionSnapshotRecord.snapshot_kind == snapshot_kind,
                        CompositionSnapshotRecord.logical_digest == logical_digest,
                    )
                )
            ).scalar_one_or_none()
        return _reference(row) if row is not None else None

    async def _publish(
        self,
        snapshot_kind: Literal["agent", "environment"],
        snapshot: Snapshot,
    ) -> SnapshotReference:
        async with self._publish_lock:
            return await self._publish_locked(snapshot_kind, snapshot)

    async def _publish_locked(
        self,
        snapshot_kind: Literal["agent", "environment"],
        snapshot: Snapshot,
    ) -> SnapshotReference:
        logical_digest = (
            snapshot.logical_agent_digest
            if isinstance(snapshot, ResolvedAgentSnapshot)
            else snapshot.logical_environment_digest
        )
        existing = await self.find(
            snapshot_kind=snapshot_kind,
            logical_digest=logical_digest,
        )
        if existing is not None:
            await self._read(existing)
            return existing

        object_kind = ObjectKind.agent_snapshot if snapshot_kind == "agent" else ObjectKind.environment_snapshot
        object_ref = await self._store.publish_object(
            object_kind=object_kind,
            object_schema_version="1",
            payload=snapshot.model_dump(mode="json"),
        )
        root_revision = (
            snapshot.root_agent if isinstance(snapshot, ResolvedAgentSnapshot) else snapshot.environment_revision
        )
        created_at = datetime.now(UTC)
        async with transaction(
            self._store.database.sessions,
            cleanup_timeout_seconds=self._store.settings.cleanup_timeout_seconds,
        ) as session:
            concurrent = (
                await session.execute(
                    select(CompositionSnapshotRecord).where(
                        CompositionSnapshotRecord.snapshot_kind == snapshot_kind,
                        CompositionSnapshotRecord.logical_digest == logical_digest,
                    )
                )
            ).scalar_one_or_none()
            if concurrent is None:
                session.add(
                    CompositionSnapshotRecord(
                        snapshot_kind=snapshot_kind,
                        logical_digest=logical_digest,
                        object_digest=object_ref.logical_digest,
                        generation_id=snapshot.generation_id,
                        root_resource_kind=root_revision.kind.value,
                        root_resource_id=root_revision.resource_id,
                        root_content_digest=root_revision.content_digest,
                        created_at=created_at,
                    )
                )
        reference = await self.find(
            snapshot_kind=snapshot_kind,
            logical_digest=logical_digest,
        )
        if reference is None:
            raise StoreIntegrityError(
                "The published snapshot reference was not retained.",
                code="snapshot_reference_missing",
            )
        return reference

    async def _read(self, reference: SnapshotReference) -> Snapshot:
        retained = await self.find(
            snapshot_kind=reference.snapshot_kind,
            logical_digest=reference.logical_digest,
        )
        if retained != reference:
            raise CompositionError(
                "The requested snapshot reference is not retained.",
                code="snapshot_missing",
            )
        object_ref = ObjectRef(
            object_kind=(
                ObjectKind.agent_snapshot if reference.snapshot_kind == "agent" else ObjectKind.environment_snapshot
            ),
            object_schema_version="1",
            logical_digest=reference.object_digest,
        )
        envelope = await self._store.read_object(object_ref)
        model_type = ResolvedAgentSnapshot if reference.snapshot_kind == "agent" else ResolvedEnvironmentSnapshot
        try:
            snapshot = model_type.model_validate(envelope.payload, strict=True)
        except ValidationError as exc:
            raise StoreIntegrityError(
                "A selected composition snapshot is invalid.",
                code="snapshot_invalid",
                details={"validation_error_count": exc.error_count()},
            ) from exc
        logical_digest = (
            snapshot.logical_agent_digest
            if isinstance(snapshot, ResolvedAgentSnapshot)
            else snapshot.logical_environment_digest
        )
        root_revision = (
            snapshot.root_agent if isinstance(snapshot, ResolvedAgentSnapshot) else snapshot.environment_revision
        )
        if (
            logical_digest != reference.logical_digest
            or snapshot.generation_id != reference.generation_id
            or root_revision != reference.root_revision
        ):
            raise StoreIntegrityError(
                "A selected composition snapshot does not match its durable index.",
                code="snapshot_reference_mismatch",
            )
        return cast(Snapshot, snapshot)


def _reference(row: CompositionSnapshotRecord) -> SnapshotReference:
    from a13n_ui.configuration import ResourceKind, ResourceRevisionRef

    try:
        return SnapshotReference(
            snapshot_kind=cast(Literal["agent", "environment"], row.snapshot_kind),
            logical_digest=row.logical_digest,
            object_digest=row.object_digest,
            generation_id=row.generation_id,
            root_revision=ResourceRevisionRef(
                kind=ResourceKind(row.root_resource_kind),
                resource_id=row.root_resource_id,
                content_digest=row.root_content_digest,
            ),
        )
    except (TypeError, ValueError) as exc:
        raise StoreIntegrityError(
            "A retained composition snapshot index is invalid.",
            code="snapshot_reference_invalid",
        ) from exc


__all__ = ["SnapshotRepository"]
