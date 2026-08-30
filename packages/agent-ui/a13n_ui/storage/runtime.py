"""Composed Agent UI local-store lifetime, ownership, and recovery."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from anyio import to_thread
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import StatementError

from a13n_ui import __version__
from a13n_ui.errors import StoreIntegrityError

from .database import Database, open_database, short_session, transaction
from .layout import StorageLayout
from .models import (
    CompositionSnapshotRecord,
    HostEnvironmentResourceRecord,
    ImmutableObjectRecord,
    PendingDeferredRecord,
    RecoveryDiagnosticRecord,
    ResourceRevisionRecord,
    SkillPackageReferenceRecord,
    ThreadCheckpointRecord,
)
from .objects import ImmutableObjectStore, ObjectEnvelope, ObjectKind, ObjectRef

if TYPE_CHECKING:
    from a13n_ui.settings import StorageSettings


class StoreDiagnostic(BaseModel):
    """Detached safe evidence produced by local-store recovery."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    diagnostic_id: int = Field(gt=0)
    process_generation: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=64)
    detail: str = Field(min_length=1, max_length=255)
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def _normalize_recorded_at(cls, value: datetime) -> datetime:
        return _as_utc(value)


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
        """Publish a file before registering its detached reference in SQLite."""

        envelope = await self.objects.publish(
            object_kind=object_kind,
            object_schema_version=object_schema_version,
            payload=payload,
            payload_codec_version=payload_codec_version,
        )
        registered_at = datetime.now(UTC)
        async with transaction(self.database.sessions) as session:
            current = await session.get(ImmutableObjectRecord, envelope.logical_digest)
            if current is None:
                session.add(
                    ImmutableObjectRecord(
                        logical_digest=envelope.logical_digest,
                        object_kind=envelope.object_kind.value,
                        object_schema_version=envelope.object_schema_version,
                        created_at=envelope.created_at,
                        registered_at=registered_at,
                    )
                )
            elif (
                current.object_kind != envelope.object_kind.value
                or current.object_schema_version != envelope.object_schema_version
                or _as_utc(current.created_at) != _as_utc(envelope.created_at)
            ):
                raise StoreIntegrityError(
                    "SQLite object registration conflicts with immutable object identity.",
                    code="object_registration_conflict",
                    details={"logical_digest": envelope.logical_digest},
                )
        return envelope.ref

    async def read_object(self, reference: ObjectRef) -> ObjectEnvelope:
        """Read an exact registered object without holding SQLite during file I/O."""

        async with short_session(self.database.sessions) as session:
            try:
                record = await session.get(ImmutableObjectRecord, reference.logical_digest)
            except (TypeError, ValueError, StatementError) as exc:
                raise StoreIntegrityError(
                    "SQLite contains an invalid immutable object registration.",
                    code="object_registration_invalid",
                    details={"logical_digest": reference.logical_digest},
                ) from exc
            if record is None:
                raise StoreIntegrityError(
                    "Selected immutable object is not registered in SQLite.",
                    code="object_not_registered",
                    details={"logical_digest": reference.logical_digest},
                )
            selected = (
                record.object_kind,
                record.object_schema_version,
                _as_utc(record.created_at),
            )
        envelope = await self.objects.read(reference)
        if selected != (
            envelope.object_kind.value,
            envelope.object_schema_version,
            _as_utc(envelope.created_at),
        ):
            raise StoreIntegrityError(
                "Selected SQLite object metadata does not match the immutable file.",
                code="object_registration_mismatch",
                details={"logical_digest": reference.logical_digest},
            )
        return envelope

    async def object_count(self) -> int:
        async with short_session(self.database.sessions) as session:
            return int((await session.execute(select(func.count()).select_from(ImmutableObjectRecord))).scalar_one())

    async def cleanup_unreferenced_objects(self, *, retention_seconds: int) -> int:
        """Delete only expired inventory objects absent from durable references."""

        if retention_seconds <= 0:
            raise ValueError("object retention must be positive")
        cutoff = datetime.now(UTC) - timedelta(seconds=retention_seconds)
        async with short_session(self.database.sessions) as session:
            referenced = set((await session.execute(select(ResourceRevisionRecord.object_digest))).scalars())
            referenced.update((await session.execute(select(SkillPackageReferenceRecord.object_digest))).scalars())
            referenced.update((await session.execute(select(CompositionSnapshotRecord.object_digest))).scalars())
            referenced.update((await session.execute(select(ThreadCheckpointRecord.state_object_digest))).scalars())
            referenced.update((await session.execute(select(PendingDeferredRecord.object_digest))).scalars())
            provider_state_digests = (
                await session.execute(
                    select(HostEnvironmentResourceRecord.selected_provider_state_digest).where(
                        HostEnvironmentResourceRecord.selected_provider_state_digest.is_not(None)
                    )
                )
            ).scalars()
            referenced.update(digest for digest in provider_state_digests if digest is not None)
            registrations = tuple(
                (
                    await session.execute(
                        select(
                            ImmutableObjectRecord.logical_digest,
                            ImmutableObjectRecord.object_kind,
                            ImmutableObjectRecord.object_schema_version,
                            ImmutableObjectRecord.registered_at,
                        )
                    )
                ).tuples()
            )
            registered_digests = {row[0] for row in registrations}
            candidates = tuple(row for row in registrations if _as_utc(row[3]) < cutoff)
        removed: list[str] = []
        for logical_digest, object_kind, object_schema_version, _registered_at in candidates:
            if logical_digest in referenced:
                continue
            try:
                reference = ObjectRef(
                    object_kind=ObjectKind(object_kind),
                    object_schema_version=object_schema_version,
                    logical_digest=logical_digest,
                )
            except ValueError as exc:
                raise StoreIntegrityError(
                    "An expired object registration is invalid.",
                    code="object_registration_invalid",
                ) from exc
            await self.objects.remove(reference)
            removed.append(logical_digest)
        if removed:
            async with transaction(self.database.sessions) as session:
                await session.execute(
                    delete(ImmutableObjectRecord).where(ImmutableObjectRecord.logical_digest.in_(removed))
                )
        unregistered = await self.objects.remove_expired_unregistered(
            registered_digests,
            cutoff=cutoff,
        )
        return len(removed) + unregistered

    async def record_recovery_diagnostic(self, *, code: str, detail: str) -> None:
        """Append bounded path-free recovery evidence from a feature-owned scan."""

        if not code or len(code) > 64 or not detail:
            raise ValueError("recovery diagnostic fields are invalid")
        async with transaction(self.database.sessions) as session:
            session.add(
                RecoveryDiagnosticRecord(
                    process_generation=self.process_generation,
                    code=code,
                    detail=detail[:255],
                    recorded_at=datetime.now(UTC),
                )
            )

    async def recovery_diagnostics(self, *, limit: int = 100) -> tuple[StoreDiagnostic, ...]:
        """Return recent recovery evidence without exposing persistence entities."""

        if not 1 <= limit <= 1000:
            raise ValueError("diagnostic limit must be between 1 and 1000")
        async with short_session(self.database.sessions) as session:
            rows = tuple(
                (
                    await session.execute(
                        select(RecoveryDiagnosticRecord)
                        .order_by(RecoveryDiagnosticRecord.diagnostic_id.desc())
                        .limit(limit)
                    )
                ).scalars()
            )
        return tuple(
            StoreDiagnostic(
                diagnostic_id=row.diagnostic_id,
                process_generation=row.process_generation,
                code=row.code,
                detail=row.detail,
                recorded_at=row.recorded_at,
            )
            for row in rows
        )


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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = ["LocalStore", "StoreDiagnostic", "open_local_store"]
