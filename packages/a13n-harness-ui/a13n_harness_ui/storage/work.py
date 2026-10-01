"""Identity-bound work query data, never execution authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import case, literal, or_, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from .database import DatabaseSessions, short_session, transaction
from .models import ThreadRecord, ThreadWorkRecord
from .objects import ObjectKind, ObjectRef

WORK_VERSION = 1


def source_id(reference: ObjectRef) -> str:
    return (
        f"initial:{reference.logical_digest}"
        if reference.object_kind is ObjectKind.thread_initial_state
        else reference.logical_digest
    )


def _selected_source() -> ColumnElement[str]:
    return case(
        (ThreadRecord.continuation_digest.is_not(None), ThreadRecord.continuation_digest),
        else_="initial:" + ThreadRecord.initial_state_digest,
    )


def _selected_schema() -> ColumnElement[str]:
    return case(
        (ThreadRecord.continuation_digest.is_not(None), ThreadRecord.continuation_schema_version),
        else_=ThreadRecord.initial_state_schema_version,
    )


@dataclass(frozen=True, slots=True)
class WorkData:
    summary_json: str
    tasks_json: str | None = None
    notes_json: str | None = None


class WorkRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def read(
        self, thread_id: str, reference: ObjectRef, include: tuple[Literal["tasks", "notes"], ...] = ()
    ) -> WorkData | None:
        # Summary polling must not fetch or decode note/task detail columns.
        columns: list[InstrumentedAttribute[str | None]] = [ThreadWorkRecord.summary_json]
        if "tasks" in include:
            columns.append(ThreadWorkRecord.tasks_json)
        if "notes" in include:
            columns.append(ThreadWorkRecord.notes_json)
        async with short_session(self._sessions) as session:
            row = (
                await session.execute(
                    select(*columns).where(
                        ThreadWorkRecord.thread_id == thread_id,
                        ThreadWorkRecord.source_id == source_id(reference),
                        ThreadWorkRecord.object_schema_version == reference.object_schema_version,
                        ThreadWorkRecord.version == WORK_VERSION,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        values = row._mapping
        return WorkData(values["summary_json"], values.get("tasks_json"), values.get("notes_json"))

    async def publish(self, thread_id: str, reference: ObjectRef, data: WorkData) -> bool:
        """Compare and replace all sections atomically without touching Thread metadata."""
        if reference.object_kind not in {ObjectKind.continuation, ObjectKind.thread_initial_state}:
            raise ValueError("Work projections require a Thread state reference")
        statement = insert(ThreadWorkRecord).from_select(
            ["thread_id", "source_id", "object_schema_version", "version", "summary_json", "tasks_json", "notes_json"],
            select(
                ThreadRecord.thread_id,
                literal(source_id(reference)),
                literal(reference.object_schema_version),
                literal(WORK_VERSION),
                literal(data.summary_json),
                literal(data.tasks_json),
                literal(data.notes_json),
            ).where(
                ThreadRecord.thread_id == thread_id,
                _selected_source() == source_id(reference),
                _selected_schema() == reference.object_schema_version,
            ),
        )
        statement = statement.on_conflict_do_update(
            index_elements=[ThreadWorkRecord.thread_id],
            set_={
                name: getattr(statement.excluded, name)
                for name in (
                    "source_id",
                    "object_schema_version",
                    "version",
                    "summary_json",
                    "tasks_json",
                    "notes_json",
                )
            },
        ).returning(ThreadWorkRecord.thread_id)
        async with transaction(self._sessions) as session:
            return (await session.execute(statement)).scalar_one_or_none() is not None

    async def missing(self, *, after: str = "", limit: int = 16) -> tuple[tuple[str, ObjectRef], ...]:
        async with short_session(self._sessions) as session:
            rows = await session.execute(
                select(
                    ThreadRecord.thread_id,
                    ThreadRecord.continuation_digest,
                    ThreadRecord.continuation_schema_version,
                    ThreadRecord.initial_state_digest,
                    ThreadRecord.initial_state_schema_version,
                )
                .outerjoin(ThreadWorkRecord)
                .where(
                    ThreadRecord.parent_thread_id.is_(None),
                    ThreadRecord.thread_id > after,
                    or_(
                        ThreadWorkRecord.thread_id.is_(None),
                        ThreadWorkRecord.source_id.is_distinct_from(_selected_source()),
                        ThreadWorkRecord.object_schema_version.is_distinct_from(_selected_schema()),
                        ThreadWorkRecord.version.is_distinct_from(WORK_VERSION),
                    ),
                )
                .order_by(ThreadRecord.thread_id)
                .limit(limit)
            )
            return tuple(
                (
                    thread_id,
                    ObjectRef(
                        object_kind=ObjectKind.continuation if digest is not None else ObjectKind.thread_initial_state,
                        object_schema_version=schema if digest is not None else initial_schema,
                        logical_digest=digest if digest is not None else initial_digest,
                    ),
                )
                for thread_id, digest, schema, initial_digest, initial_schema in rows
            )
