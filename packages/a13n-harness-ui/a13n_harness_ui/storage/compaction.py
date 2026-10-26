"""Durable, conditionally completed Harness compaction operations."""

from a13n_harness.compaction import CompactionOperation
from sqlalchemy import delete, update
from sqlalchemy.dialects.sqlite import insert

from .database import DatabaseSessions, short_session, transaction
from .models import CompactionRecord


class CompactionRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def get(self, thread_id: str) -> CompactionOperation | None:
        async with short_session(self._sessions) as session:
            row = await session.get(CompactionRecord, thread_id)
            return CompactionOperation.model_validate_json(row.payload) if row is not None else None

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
                )
                .on_conflict_do_nothing(index_elements=[CompactionRecord.thread_id])
            )
            row = await session.get(CompactionRecord, basis.thread_id)
            assert row is not None
            return CompactionOperation.model_validate_json(row.payload)

    async def finish(self, operation: CompactionOperation) -> bool:
        """Publish only while this exact operation is still running."""
        basis = operation.input.basis
        async with transaction(self._sessions) as session:
            selected = await session.scalar(
                update(CompactionRecord)
                .where(
                    CompactionRecord.thread_id == basis.thread_id,
                    CompactionRecord.operation_id == basis.operation_id,
                    CompactionRecord.status == "running",
                )
                .values(status=operation.status, payload=operation.model_dump_json())
                .returning(CompactionRecord.operation_id)
            )
            return selected is not None

    async def retire(self, thread_id: str, operation_id: str) -> None:
        async with transaction(self._sessions) as session:
            await session.execute(
                delete(CompactionRecord).where(
                    CompactionRecord.thread_id == thread_id,
                    CompactionRecord.operation_id == operation_id,
                )
            )
