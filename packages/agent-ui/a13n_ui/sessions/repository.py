"""Short-transaction persistence for continuation-backed Sessions."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import select

from a13n_ui.composition import ResolvedEnvironmentSnapshot, SnapshotReference
from a13n_ui.configuration import ResourceKind, ResourceRevisionRef
from a13n_ui.errors import SessionError, StoreIntegrityError
from a13n_ui.storage.database import short_session, transaction
from a13n_ui.storage.models import CompositionSnapshotRecord, SessionRecord
from a13n_ui.storage.runtime import LocalStore

from .models import (
    ContinuationRef,
    LocalSession,
    SessionAgentSkillSelection,
    SessionForkRef,
    SessionSummary,
    SessionUpdate,
)


class SessionRepository:
    """Persist only Session metadata and the latest continuation reference."""

    def __init__(self, store: LocalStore) -> None:
        self._store = store

    async def create(
        self,
        *,
        session_id: str,
        agent_snapshot: SnapshotReference,
        environment_snapshot: SnapshotReference,
        resolved_environment: ResolvedEnvironmentSnapshot,
        title: str | None,
        skill_selections: Sequence[SessionAgentSkillSelection],
        continuation: ContinuationRef,
        parent_fork: SessionForkRef | None = None,
    ) -> LocalSession:
        now = datetime.now(UTC)
        async with transaction(self._store.database.sessions) as database_session:
            database_session.add(
                SessionRecord(
                    session_id=session_id,
                    created_at=now,
                    updated_at=now,
                    title=title,
                    archived_at=None,
                    pinned=False,
                    agent_snapshot_id=await self._snapshot_id(database_session, agent_snapshot, "agent"),
                    environment_snapshot_id=await self._snapshot_id(
                        database_session,
                        environment_snapshot,
                        "environment",
                    ),
                    skill_selections_json=_json([item.model_dump(mode="json") for item in skill_selections]),
                    parent_fork_json=(_json(parent_fork.model_dump(mode="json")) if parent_fork is not None else None),
                    continuation_object_digest=continuation.object_digest,
                )
            )
            await database_session.flush()
            from a13n_ui.environments.repository import seed_environment_resources

            await seed_environment_resources(
                database_session,
                session_id=session_id,
                snapshot=resolved_environment,
                created_at=now,
            )
        return await self.get(session_id)

    async def get(self, session_id: str) -> LocalSession:
        async with short_session(self._store.database.sessions) as database_session:
            session_row = await database_session.get(SessionRecord, session_id)
            if session_row is None:
                raise SessionError("The selected Session does not exist.", code="session_missing")
            agent_row = await database_session.get(CompositionSnapshotRecord, session_row.agent_snapshot_id)
            environment_row = await database_session.get(
                CompositionSnapshotRecord,
                session_row.environment_snapshot_id,
            )
        if agent_row is None or environment_row is None:
            raise StoreIntegrityError(
                "A Session references a missing composition snapshot.",
                code="session_snapshot_missing",
            )
        try:
            return LocalSession(
                session_id=session_row.session_id,
                created_at=session_row.created_at,
                updated_at=session_row.updated_at,
                title=session_row.title,
                archived_at=session_row.archived_at,
                pinned=session_row.pinned,
                agent_snapshot=_snapshot_reference(agent_row),
                environment_snapshot=_snapshot_reference(environment_row),
                skill_selections=tuple(
                    SessionAgentSkillSelection.model_validate(item, strict=True)
                    for item in _require_list(_load_json(session_row.skill_selections_json))
                ),
                parent_fork=(
                    SessionForkRef.model_validate(_load_json(session_row.parent_fork_json), strict=True)
                    if session_row.parent_fork_json is not None
                    else None
                ),
                continuation=ContinuationRef(object_digest=session_row.continuation_object_digest),
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise StoreIntegrityError(
                "A retained Session is invalid.",
                code="session_invalid",
            ) from exc

    async def list(
        self,
        *,
        include_archived: bool = False,
        limit: int = 100,
    ) -> tuple[SessionSummary, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("Session list limit must be between 1 and 1000")
        async with short_session(self._store.database.sessions) as database_session:
            statement = select(SessionRecord)
            if not include_archived:
                statement = statement.where(SessionRecord.archived_at.is_(None))
            rows = tuple(
                (
                    await database_session.execute(
                        statement.order_by(
                            SessionRecord.pinned.desc(),
                            SessionRecord.updated_at.desc(),
                        ).limit(limit)
                    )
                ).scalars()
            )
        return tuple(
            SessionSummary(
                session_id=row.session_id,
                title=row.title,
                archived_at=row.archived_at,
                pinned=row.pinned,
                updated_at=row.updated_at,
            )
            for row in rows
        )

    async def update(self, session_id: str, update: SessionUpdate) -> LocalSession:
        now = datetime.now(UTC)
        async with transaction(self._store.database.sessions) as database_session:
            record = await database_session.get(SessionRecord, session_id)
            if record is None:
                raise SessionError("The selected Session does not exist.", code="session_missing")
            fields = update.model_fields_set
            if "title" in fields:
                record.title = update.title
            if "archived" in fields and update.archived is not None:
                record.archived_at = now if update.archived else None
            if "pinned" in fields and update.pinned is not None:
                record.pinned = update.pinned
            record.updated_at = now
        return await self.get(session_id)

    async def select_continuation(self, session_id: str, continuation: ContinuationRef) -> LocalSession:
        """Store the latest complete continuation using last-write-wins."""

        async with transaction(self._store.database.sessions) as database_session:
            record = await database_session.get(SessionRecord, session_id)
            if record is None:
                raise SessionError("The selected Session does not exist.", code="session_missing")
            record.continuation_object_digest = continuation.object_digest
            record.updated_at = datetime.now(UTC)
        return await self.get(session_id)

    async def hard_delete(self, session_id: str) -> None:
        async with transaction(self._store.database.sessions) as database_session:
            record = await database_session.get(SessionRecord, session_id)
            if record is None:
                raise SessionError("The selected Session does not exist.", code="session_missing")
            await database_session.delete(record)

    async def _snapshot_id(
        self,
        database_session: object,
        reference: SnapshotReference,
        expected_kind: str,
    ) -> int:
        from sqlalchemy.ext.asyncio import AsyncSession

        if not isinstance(database_session, AsyncSession):
            raise TypeError("database_session must be an AsyncSession")
        row = (
            await database_session.execute(
                select(CompositionSnapshotRecord).where(
                    CompositionSnapshotRecord.snapshot_kind == expected_kind,
                    CompositionSnapshotRecord.logical_digest == reference.logical_digest,
                    CompositionSnapshotRecord.object_digest == reference.object_digest,
                    CompositionSnapshotRecord.generation_id == reference.generation_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise SessionError(
                "The selected composition snapshot is not retained.",
                code="session_snapshot_missing",
            )
        return row.snapshot_id


def _snapshot_reference(row: CompositionSnapshotRecord) -> SnapshotReference:
    try:
        kind = ResourceKind(row.root_resource_kind)
    except ValueError as exc:
        raise StoreIntegrityError(
            "A composition snapshot has an invalid root resource kind.",
            code="snapshot_reference_invalid",
        ) from exc
    snapshot_kind: Literal["agent", "environment"]
    if row.snapshot_kind == "agent":
        snapshot_kind = "agent"
    elif row.snapshot_kind == "environment":
        snapshot_kind = "environment"
    else:
        raise StoreIntegrityError(
            "A composition snapshot has an invalid snapshot kind.",
            code="snapshot_reference_invalid",
        )
    return SnapshotReference(
        snapshot_kind=snapshot_kind,
        logical_digest=row.logical_digest,
        object_digest=row.object_digest,
        generation_id=row.generation_id,
        root_revision=ResourceRevisionRef(
            kind=kind,
            resource_id=row.root_resource_id,
            content_digest=row.root_content_digest,
        ),
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _load_json(value: str | None) -> object:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError) as exc:
        raise StoreIntegrityError("Stored Session JSON is invalid.", code="session_json_invalid") from exc


def _require_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise StoreIntegrityError("Stored Session selections are invalid.", code="session_json_invalid")
    return value


__all__ = ["SessionRepository"]
