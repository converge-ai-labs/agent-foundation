"""Short SQLite publication transactions; comment rows pin existing immutable sources."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_harness_ui.errors import StoreConflictError, ThreadError
from a13n_harness_ui.output_comment_models import (
    ChildOutputLocation,
    CommentPublication,
    OutputComment,
    SavedOutputTarget,
)

from .database import short_session, transaction
from .models import ChildExecutionRecord, OutputCommentRecord, ThreadRecord
from .objects import ObjectKind, ObjectRef


def target_key(target: SavedOutputTarget) -> str:
    return hashlib.sha256(target.model_dump_json().encode("utf-8")).hexdigest()


def _value(row: OutputCommentRecord) -> OutputComment:
    publication = CommentPublication.model_validate_json(row.publication_json)
    return OutputComment(**publication.model_dump(), root_thread_id=row.root_thread_id, created_at=row.created_at)


def _reconcile(row: OutputCommentRecord, root_thread_id: str, publication: CommentPublication) -> OutputComment:
    if (
        row.root_thread_id != root_thread_id
        or CommentPublication.model_validate_json(row.publication_json) != publication
    ):
        raise StoreConflictError("Comment identity already has different content.", code="comment_identity_conflict")
    return _value(row)


class OutputCommentRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get(self, root_thread_id: str, comment_id: str) -> OutputComment | None:
        async with short_session(self._sessions) as session:
            row = await session.get(OutputCommentRecord, comment_id)
            return _value(row) if row is not None and row.root_thread_id == root_thread_id else None

    async def reconcile(self, root_thread_id: str, publication: CommentPublication) -> OutputComment | None:
        async with short_session(self._sessions) as session:
            row = await session.get(OutputCommentRecord, publication.comment_id)
            return _reconcile(row, root_thread_id, publication) if row is not None else None

    async def retained_source(self, root_thread_id: str, target: SavedOutputTarget) -> ObjectRef | None:
        async with short_session(self._sessions) as session:
            row = (
                await session.execute(
                    select(OutputCommentRecord)
                    .where(
                        OutputCommentRecord.root_thread_id == root_thread_id,
                        OutputCommentRecord.target_key == target_key(target),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return ObjectRef(
                object_kind=ObjectKind(row.source_kind),
                object_schema_version=row.source_schema_version,
                logical_digest=row.source_digest,
            )

    async def publish(self, root_thread_id: str, publication: CommentPublication, source: ObjectRef) -> OutputComment:
        async with transaction(self._sessions) as session:
            existing = await session.get(OutputCommentRecord, publication.comment_id)
            if existing is not None:
                return _reconcile(existing, root_thread_id, publication)
            target = publication.target
            key = target_key(target)
            retained = (
                await session.execute(
                    select(OutputCommentRecord.comment_id)
                    .where(
                        OutputCommentRecord.root_thread_id == root_thread_id,
                        OutputCommentRecord.target_key == key,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if retained is None:
                if isinstance(target.location, ChildOutputLocation):
                    child = await session.get(ChildExecutionRecord, target.location.execution_id)
                    matches = (
                        child is not None
                        and child.child_thread_id == target.producing_thread_id
                        and child.selected_checkpoint_digest == source.logical_digest
                        and child.selected_checkpoint_schema_version == source.object_schema_version
                    )
                else:
                    thread = await session.get(ThreadRecord, root_thread_id)
                    matches = (
                        thread is not None
                        and thread.parent_thread_id is None
                        and thread.continuation_digest == source.logical_digest
                        and thread.continuation_schema_version == source.object_schema_version
                    )
                if not matches:
                    raise StoreConflictError(
                        "Saved output selection changed; refetch before publishing.", code="comment_target_stale"
                    )
            row = OutputCommentRecord(
                comment_id=publication.comment_id,
                root_thread_id=root_thread_id,
                producing_thread_id=target.producing_thread_id,
                target_key=key,
                source_kind=source.object_kind.value,
                source_schema_version=source.object_schema_version,
                source_digest=source.logical_digest,
                publication_json=publication.model_dump_json(),
                created_at=datetime.now(UTC),
            )
            session.add(row)
            await session.flush()
            result = _value(row)
        return result

    async def list(
        self, root_thread_id: str, *, target: SavedOutputTarget | None, after: tuple[datetime, str] | None, limit: int
    ) -> tuple[OutputComment, ...]:
        statement = select(OutputCommentRecord).where(OutputCommentRecord.root_thread_id == root_thread_id)
        if target is not None:
            statement = statement.where(OutputCommentRecord.target_key == target_key(target))
        if after is not None:
            timestamp, identity = after
            statement = statement.where(
                or_(
                    OutputCommentRecord.created_at > timestamp,
                    and_(OutputCommentRecord.created_at == timestamp, OutputCommentRecord.comment_id > identity),
                )
            )
        statement = statement.order_by(OutputCommentRecord.created_at, OutputCommentRecord.comment_id).limit(limit)
        async with short_session(self._sessions) as session:
            return tuple(_value(row) for row in (await session.execute(statement)).scalars())

    async def require_root(self, root_thread_id: str) -> None:
        async with short_session(self._sessions) as session:
            row = await session.get(ThreadRecord, root_thread_id)
            if row is None or row.parent_thread_id is not None:
                raise ThreadError("Comments require an existing root Thread.", code="comment_thread_invalid")
