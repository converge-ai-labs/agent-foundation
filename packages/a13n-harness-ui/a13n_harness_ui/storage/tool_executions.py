"""Durable retained calls and completion input, separate from Thread continuation heads."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from a13n_harness.content import request_input_content
from a13n_harness.environment.process_recovery import ShellExecutionState
from a13n_harness.tool_execution import StoredToolExecution, ToolExecutionRecord
from a13n_harness.tool_execution_payload import ToolExecutionPayload
from a13n_logging import get_logger
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ModelRequest
from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_harness_ui.errors import ObjectIntegrityError

from .checkpoint_lock import checkpoint_lock
from .database import DatabaseSessions, short_session, transaction
from .models import DeletedThreadRecord, ToolExecutionHeadRecord
from .objects import ImmutableObjectStore, ObjectKind, ObjectRef
from .ownership import OwnershipLocks

RESULT_RETENTION = timedelta(hours=1)
MAX_RESULT_RETENTION = timedelta(days=1)

_COMPOSITION: ContextVar[ObjectRef | None] = ContextVar("ui_tool_execution_composition", default=None)


@contextmanager
def execution_composition(reference: ObjectRef) -> Iterator[None]:
    """Capture the original Run selection in every admitted tool task."""
    token = _COMPOSITION.set(reference)
    try:
        yield
    finally:
        _COMPOSITION.reset(token)


class ExecutionPayload(BaseModel):
    execution_id: str
    thread_id: str
    result: ToolExecutionPayload
    shell: ShellExecutionState | None = None


def observation(row: ToolExecutionHeadRecord) -> ToolExecutionRecord:
    record = ToolExecutionRecord.model_validate_json(row.record_json)
    return record.model_copy(
        update={
            "foreground_released": row.foreground_released,
            "delivery_acknowledged": row.delivery_acknowledged,
            "result_available": record.result_available
            and row.payload_digest is not None
            and (row.expires_at is None or row.expires_at > datetime.now(UTC)),
        }
    )


def payload_ref(row: ToolExecutionHeadRecord) -> ObjectRef:
    assert row.payload_digest is not None
    return ObjectRef(
        object_kind=ObjectKind.tool_execution, object_schema_version="1", logical_digest=row.payload_digest
    )


def consumed(row: ToolExecutionHeadRecord, at: datetime) -> None:
    row.completion_state = "consumed"
    if row.consumed_at is None:
        row.consumed_at = at
        if row.expires_at is not None:
            row.expires_at = min(row.expires_at, at + RESULT_RETENTION)


def notify(row: ToolExecutionHeadRecord) -> None:
    if row.terminal and row.foreground_released and row.completion_state == "waiting":
        row.completion_state = "pending"
    if row.delivery_acknowledged:
        consumed(row, datetime.now(UTC))


def completion_ids(messages: Sequence[ModelMessage]) -> tuple[str, ...]:
    """Read Host-authored completion identities actually incorporated in history."""
    result: set[str] = set()
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for item in request_input_content(message):
            if item.metadata.source_id != "tool_execution.completion":
                continue
            ids = (item.metadata.model_extra or {}).get("execution_ids")
            if isinstance(ids, list):
                result.update(value for value in ids if isinstance(value, str))
    return tuple(sorted(result))


async def consume_completions(session: AsyncSession, thread_id: str, execution_ids: Sequence[str]) -> None:
    """Join the checkpoint transaction that incorporates completion input."""
    if execution_ids:
        rows = await session.scalars(
            select(ToolExecutionHeadRecord).where(
                ToolExecutionHeadRecord.thread_id == thread_id,
                ToolExecutionHeadRecord.execution_id.in_(execution_ids),
                ToolExecutionHeadRecord.completion_state == "pending",
            )
        )
        at = datetime.now(UTC)
        for row in rows:
            consumed(row, at)


class ToolExecutionRepository:
    """Publish native payload before selecting its head and completion intent atomically."""

    def __init__(self, sessions: DatabaseSessions, objects: ImmutableObjectStore, root: Path) -> None:
        self.sessions, self.objects = sessions, objects
        self.owners = OwnershipLocks(root / "execution-locks")
        self.completion_owners = OwnershipLocks(root / "completion-locks")
        self._root = root / "tool-execution-files"
        self._prune_cursor: str | None = None

    async def save(self, execution: StoredToolExecution) -> None:
        record = execution.record
        if not await self.owners.acquire(record.execution_id):
            raise RuntimeError("Another App owns this tool execution")
        async with checkpoint_lock(self._root, record.execution_id):
            terminal = await self._save(execution)
        if terminal:
            await self.owners.release(record.execution_id)

    async def _save(self, execution: StoredToolExecution) -> bool:
        record = execution.record
        async with transaction(self.sessions) as session:
            if await session.get(DeletedThreadRecord, record.thread_id) is not None:
                raise ValueError("Execution belongs to a deleted Thread")
            row = await session.get(ToolExecutionHeadRecord, record.execution_id)
            if row is None and record.created_at + MAX_RESULT_RETENTION <= datetime.now(UTC):
                # Only fresh admissions create heads. Old owners cannot recreate
                # a collected execution after its bounded tombstone window.
                return True
            if row is not None and row.thread_id != record.thread_id:
                raise ValueError("Execution belongs to another Thread")
            if row is not None and row.terminal:
                row.foreground_released |= record.foreground_released
                row.delivery_acknowledged |= record.delivery_acknowledged
                notify(row)
                return True
        payload = ExecutionPayload(
            execution_id=record.execution_id,
            thread_id=record.thread_id,
            result=ToolExecutionPayload.from_execution(execution),
            shell=execution.shell,
        )
        reference = (await self.objects.publish_model(object_kind=ObjectKind.tool_execution, value=payload)).ref
        async with transaction(self.sessions) as session:
            if await session.get(DeletedThreadRecord, record.thread_id) is not None:
                raise ValueError("Execution belongs to a deleted Thread")
            row = await session.get(ToolExecutionHeadRecord, record.execution_id)
            if row is None:
                composition = _COMPOSITION.get()
                row = ToolExecutionHeadRecord(
                    execution_id=record.execution_id,
                    thread_id=record.thread_id,
                    record_json=record.model_dump_json(),
                    payload_digest=reference.logical_digest,
                    composition_json=composition.model_dump_json() if composition is not None else None,
                    terminal=record.terminal,
                    created_at=record.created_at,
                    foreground_released=record.foreground_released,
                    delivery_acknowledged=record.delivery_acknowledged,
                    completion_state="waiting",
                    expires_at=(record.completed_at or datetime.now(UTC)) + MAX_RESULT_RETENTION
                    if record.terminal
                    else None,
                )
                session.add(row)
            else:
                if row.thread_id != record.thread_id:
                    raise ValueError("Execution belongs to another Thread")
                # Final result is immutable. Receipt and consumption can advance
                # independently while a native payload is being published.
                if not row.terminal:
                    record = record.model_copy(
                        update={"cancel_requested": record.cancel_requested or observation(row).cancel_requested}
                    )
                    row.record_json = record.model_dump_json()
                    row.payload_digest = reference.logical_digest
                    row.terminal = record.terminal
                    if record.terminal:
                        row.expires_at = (record.completed_at or datetime.now(UTC)) + MAX_RESULT_RETENTION
                row.foreground_released |= record.foreground_released
                row.delivery_acknowledged |= record.delivery_acknowledged
            notify(row)
        return record.terminal

    async def get(self, thread_id: str, execution_id: str) -> StoredToolExecution | None:
        async with checkpoint_lock(self._root, execution_id):
            async with short_session(self.sessions) as session:
                row = await session.get(ToolExecutionHeadRecord, execution_id)
                if row is None or row.thread_id != thread_id:
                    return None
                record = observation(row)
                if row.payload_digest is None or (row.expires_at is not None and row.expires_at <= datetime.now(UTC)):
                    return StoredToolExecution(record)
                reference = payload_ref(row)
            payload = await self.objects.read_model(reference, ExecutionPayload)
            return replace(payload.result.execution(record), shell=payload.shell)

    async def output_expired(self, thread_id: str, execution_id: str) -> bool:
        """Report the existing retention boundary without consuming or extending it."""
        async with short_session(self.sessions) as session:
            row = await session.get(ToolExecutionHeadRecord, execution_id)
            return bool(
                row is not None
                and row.thread_id == thread_id
                and row.expires_at is not None
                and row.expires_at <= datetime.now(UTC)
            )

    async def prune(self, *, limit: int = 100) -> int:
        """Collect payloads and expired heads through the existing bounded maintenance sweep."""
        async with transaction(self.sessions) as session:
            expired = (
                select(ToolExecutionHeadRecord.execution_id)
                .where(ToolExecutionHeadRecord.expires_at <= datetime.now(UTC) - MAX_RESULT_RETENTION)
                .order_by(ToolExecutionHeadRecord.expires_at, ToolExecutionHeadRecord.execution_id)
                .limit(limit)
            )
            await session.execute(
                delete(ToolExecutionHeadRecord).where(ToolExecutionHeadRecord.execution_id.in_(expired))
            )
        references = sorted(
            (ref for ref in await self.objects.references() if ref.object_kind == ObjectKind.tool_execution),
            key=lambda ref: ref.logical_digest,
        )
        remaining = [ref for ref in references if self._prune_cursor is None or ref.logical_digest > self._prune_cursor]
        removed = 0
        for reference in remaining[:limit]:
            self._prune_cursor = reference.logical_digest
            try:
                # Ownership is part of the payload identity, so identical results
                # in unrelated executions never share a collectable object.
                payload = await self.objects.read_model(reference, ExecutionPayload)
                async with checkpoint_lock(self._root, payload.execution_id):
                    async with transaction(self.sessions) as session:
                        row = await session.get(ToolExecutionHeadRecord, payload.execution_id)
                        if row is not None and row.payload_digest == reference.logical_digest:
                            if row.expires_at is None or row.expires_at > datetime.now(UTC):
                                continue
                            row.payload_digest = None
                            row.composition_json = None
                            if row.completion_state in {"waiting", "pending"}:
                                row.completion_state = "expired"
                    # Commit the released reference before file deletion. A crash
                    # here leaves an orphan that the next scan can safely remove.
                    await self.objects.remove(reference)
                    removed += 1
            except (OSError, ObjectIntegrityError) as exc:
                get_logger(__name__).warning("Could not prune tool payload", extra={"error_type": type(exc).__name__})
        if len(remaining) <= limit:
            self._prune_cursor = None
        return removed

    async def composition(self, thread_id: str, execution_id: str) -> ObjectRef | None:
        async with short_session(self.sessions) as session:
            row = await session.get(ToolExecutionHeadRecord, execution_id)
            if row is None or row.thread_id != thread_id or row.composition_json is None:
                return None
            return ObjectRef.model_validate_json(row.composition_json)

    async def list(self, thread_id: str, *, offset: int, limit: int) -> tuple[ToolExecutionRecord, ...]:
        priority = case(
            (ToolExecutionHeadRecord.terminal.is_(False), 0),
            (
                and_(
                    ToolExecutionHeadRecord.delivery_acknowledged.is_(False),
                    ToolExecutionHeadRecord.payload_digest.is_not(None),
                    or_(
                        ToolExecutionHeadRecord.expires_at.is_(None),
                        ToolExecutionHeadRecord.expires_at > datetime.now(UTC),
                    ),
                    func.json_extract(ToolExecutionHeadRecord.record_json, "$.result_available") == 1,
                ),
                1,
            ),
            (
                func.json_extract(ToolExecutionHeadRecord.record_json, "$.status").in_(("failed", "cancelled", "lost")),
                2,
            ),
            else_=3,
        )
        async with short_session(self.sessions) as session:
            rows = await session.scalars(
                select(ToolExecutionHeadRecord)
                .where(ToolExecutionHeadRecord.thread_id == thread_id)
                .order_by(priority, ToolExecutionHeadRecord.created_at.desc(), ToolExecutionHeadRecord.execution_id)
                .offset(offset)
                .limit(limit)
            )
            return tuple(observation(row) for row in rows)

    async def active(self, *, after: str = "", limit: int = 100) -> tuple[ToolExecutionRecord, ...]:
        async with short_session(self.sessions) as session:
            rows = await session.scalars(
                select(ToolExecutionHeadRecord)
                .where(ToolExecutionHeadRecord.terminal.is_(False), ToolExecutionHeadRecord.execution_id > after)
                .order_by(ToolExecutionHeadRecord.execution_id)
                .limit(limit)
            )
            return tuple(observation(row) for row in rows)

    async def release_foreground(self, thread_id: str, execution_id: str) -> None:
        async with transaction(self.sessions) as session:
            row = await session.get(ToolExecutionHeadRecord, execution_id)
            if row is not None and row.thread_id == thread_id:
                row.foreground_released = True
                notify(row)

    async def request_cancellation(self, thread_id: str, execution_id: str) -> None:
        async with transaction(self.sessions) as session:
            row = await session.get(ToolExecutionHeadRecord, execution_id)
            if row is not None and row.thread_id == thread_id and not row.terminal:
                row.record_json = observation(row).model_copy(update={"cancel_requested": True}).model_dump_json()

    async def acknowledge_delivery(self, thread_id: str, execution_id: str) -> None:
        async with transaction(self.sessions) as session:
            row = await session.get(ToolExecutionHeadRecord, execution_id)
            if row is not None and row.thread_id == thread_id and observation(row).result_available:
                row.delivery_acknowledged = True
                consumed(row, datetime.now(UTC))

    async def pending(
        self, thread_id: str, *, exclude: Sequence[str] = (), limit: int = 256
    ) -> tuple[ToolExecutionRecord, ...]:
        async with short_session(self.sessions) as session:
            rows = await session.scalars(
                select(ToolExecutionHeadRecord)
                .where(
                    ToolExecutionHeadRecord.thread_id == thread_id,
                    ToolExecutionHeadRecord.completion_state == "pending",
                    ToolExecutionHeadRecord.expires_at > datetime.now(UTC),
                    ToolExecutionHeadRecord.execution_id.not_in(exclude),
                )
                .order_by(ToolExecutionHeadRecord.execution_id)
                .limit(limit)
            )
            return tuple(observation(row) for row in rows)

    async def pending_threads(self, *, after: str = "", limit: int = 100) -> tuple[str, ...]:
        async with short_session(self.sessions) as session:
            return tuple(
                await session.scalars(
                    select(ToolExecutionHeadRecord.thread_id)
                    .where(
                        ToolExecutionHeadRecord.completion_state == "pending",
                        ToolExecutionHeadRecord.expires_at > datetime.now(UTC),
                        ToolExecutionHeadRecord.thread_id > after,
                    )
                    .distinct()
                    .order_by(ToolExecutionHeadRecord.thread_id)
                    .limit(limit)
                )
            )
