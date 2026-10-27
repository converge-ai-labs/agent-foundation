"""Durable generation outcomes, checkpoint settlement, and bounded pruning."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from a13n_harness import HarnessState
from a13n_harness.compaction import CompactionOperation, compaction_disposition
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .database import DatabaseSessions, short_session, transaction
from .models import CompactionRecord


@dataclass(frozen=True)
class CompactionSettlement:
    operation_id: str
    disposition: Literal["applied", "skipped"]


async def settle_compaction(
    session: AsyncSession,
    thread_id: str,
    *,
    settlement: CompactionSettlement | None = None,
    clear: bool = False,
) -> None:
    """Join the caller's checkpoint-selection transaction."""
    if not clear and settlement is None:
        return
    statement = update(CompactionRecord).where(
        CompactionRecord.thread_id == thread_id, CompactionRecord.settled_at.is_(None)
    )
    if clear:
        disposition = "cleared"
    else:
        assert settlement is not None
        statement = statement.where(CompactionRecord.operation_id == settlement.operation_id)
        disposition = settlement.disposition
    await session.execute(statement.values(settled_at=datetime.now(UTC), disposition=disposition))


class CompactionRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def get(self, thread_id: str) -> CompactionOperation | None:
        async with short_session(self._sessions) as session:
            row = await session.scalar(
                select(CompactionRecord).where(
                    CompactionRecord.thread_id == thread_id, CompactionRecord.settled_at.is_(None)
                )
            )
            return CompactionOperation.model_validate_json(row.payload) if row is not None else None

    async def settlement(self, state: HarnessState) -> CompactionSettlement | None:
        """Prepare a matching native marker; only checkpoint commit may settle it."""
        operation = await self.get(state.thread_id)
        if operation is None:
            return None
        disposition = await compaction_disposition(state, basis=operation.input.basis)
        return CompactionSettlement(operation.input.basis.operation_id, disposition) if disposition else None

    async def admit(self, operation: CompactionOperation) -> CompactionOperation:
        basis = operation.input.basis
        async with transaction(self._sessions) as session:
            await session.execute(
                insert(CompactionRecord)
                .values(
                    thread_id=basis.thread_id,
                    operation_id=basis.operation_id,
                    status=operation.status,
                    payload=operation.model_dump_json(),
                    created_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing(
                    index_elements=[CompactionRecord.thread_id], index_where=CompactionRecord.settled_at.is_(None)
                )
            )
            row = await session.scalar(
                select(CompactionRecord).where(
                    CompactionRecord.thread_id == basis.thread_id, CompactionRecord.settled_at.is_(None)
                )
            )
            assert row is not None
            return CompactionOperation.model_validate_json(row.payload)

    async def finish(self, operation: CompactionOperation) -> bool:
        """Publish only while this exact operation is still running and unsettled."""
        basis = operation.input.basis
        async with transaction(self._sessions) as session:
            selected = await session.scalar(
                update(CompactionRecord)
                .where(
                    CompactionRecord.thread_id == basis.thread_id,
                    CompactionRecord.operation_id == basis.operation_id,
                    CompactionRecord.status == "running",
                    CompactionRecord.settled_at.is_(None),
                )
                .values(status=operation.status, payload=operation.model_dump_json())
                .returning(CompactionRecord.operation_id)
            )
            return selected is not None

    async def expired_candidates(self, cutoff: datetime, *, after: str = "", limit: int = 100) -> list[tuple[str, str]]:
        async with short_session(self._sessions) as session:
            rows = await session.execute(
                select(CompactionRecord.operation_id, CompactionRecord.thread_id)
                .where(
                    CompactionRecord.settled_at.is_(None),
                    CompactionRecord.created_at < cutoff,
                    CompactionRecord.operation_id > after,
                )
                .order_by(CompactionRecord.operation_id)
                .limit(limit)
            )
            return [(operation_id, thread_id) for operation_id, thread_id in rows]

    async def expire(self, operation_id: str, cutoff: datetime) -> None:
        """Caller holds the Thread-use gate and proves no consumer or worker owns it."""
        async with transaction(self._sessions) as session:
            await session.execute(
                update(CompactionRecord)
                .where(
                    CompactionRecord.operation_id == operation_id,
                    CompactionRecord.settled_at.is_(None),
                    CompactionRecord.created_at < cutoff,
                )
                .values(settled_at=datetime.now(UTC), disposition="expired")
            )

    async def prune(self, *, limit: int = 100) -> int:
        """Delete one bounded batch of settled rows, independently of admission."""
        async with transaction(self._sessions) as session:
            candidates = (
                select(CompactionRecord.operation_id)
                .where(CompactionRecord.settled_at.is_not(None))
                .order_by(CompactionRecord.settled_at, CompactionRecord.operation_id)
                .limit(limit)
            )
            removed = await session.scalars(
                delete(CompactionRecord)
                .where(CompactionRecord.operation_id.in_(candidates))
                .returning(CompactionRecord.operation_id)
            )
            return len(removed.all())
