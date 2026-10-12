"""Best-effort record write attribution; provider content remains the source of truth.

No database transaction spans provider I/O. Confirmation time orders observations, not remote mutations.
An interruption or direct provider write may leave this index missing or stale.
"""

import hashlib
from collections.abc import Sequence

from a13n_harness.providers.memory import MemoryRecord
from a13n_logging import get_logger
from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.resources.memories.schemas import MemoryRecordProvenance, MemoryRecordSource, MemoryRecordView
from a13n_service.resources.memories.tables import MemoryRecordSourceRow
from a13n_service.tenancy.tables import PrincipalRow

logger = get_logger(__name__)
_FIELDS = ("principal_id", "run_id", "attempt_id", "tool_call_id", "confirmed_at")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def remember(
    storage: Storage, memory_id: str, item: MemoryRecord, source: MemoryRecordSource, *, created: bool
) -> None:
    """Never turn a confirmed provider write into a failed/retryable memory operation."""
    fields = source.model_dump(exclude={"principal_name"})
    values = {f"last_write_{key}": value for key, value in fields.items()}
    values.update({f"created_{key}": value if created else None for key, value in fields.items()})
    table = MemoryRecordSourceRow.__table__
    statement = insert(MemoryRecordSourceRow).values(
        memory_id=memory_id, record_id=item.id, content_hash=_digest(item.text), **values
    )
    incoming = statement.excluded
    newer = incoming.last_write_confirmed_at >= table.c.last_write_confirmed_at
    changes = {
        f"last_write_{key}": case((newer, incoming[f"last_write_{key}"]), else_=table.c[f"last_write_{key}"])
        for key in _FIELDS
    }
    changes["content_hash"] = case((newer, incoming.content_hash), else_=table.c.content_hash)
    # An update may finish indexing before the creating call does. Never invent creation on an update.
    for key in _FIELDS:
        changes[f"created_{key}"] = case(
            (table.c.created_confirmed_at.is_(None), incoming[f"created_{key}"]), else_=table.c[f"created_{key}"]
        )
    try:
        async with transaction(storage) as session:
            await session.execute(
                statement.on_conflict_do_update(index_elements=["memory_id", "record_id"], set_=changes)
            )
    except SQLAlchemyError:
        logger.warning("Record source could not be saved", extra={"memory_id": memory_id, "record_id": item.id})


async def forget(storage: Storage, memory_id: str, record_id: str) -> None:
    try:
        async with transaction(storage) as session:
            await session.execute(
                delete(MemoryRecordSourceRow).where(
                    MemoryRecordSourceRow.memory_id == memory_id, MemoryRecordSourceRow.record_id == record_id
                )
            )
    except SQLAlchemyError:
        logger.warning("Record source could not be removed", extra={"memory_id": memory_id, "record_id": record_id})


def _source(row: MemoryRecordSourceRow, prefix: str, names: dict[str, str]) -> MemoryRecordSource:
    fields = {key: getattr(row, f"{prefix}_{key}") for key in _FIELDS}
    return MemoryRecordSource.model_validate({**fields, "principal_name": names.get(fields["principal_id"])})


async def views(storage: Storage, memory_id: str, items: Sequence[MemoryRecord]) -> list[MemoryRecordView]:
    """Enrich an already authorized provider page with one bounded source query and one identity query."""
    result = [MemoryRecordView.model_validate(item) for item in items]
    if not items:
        return result
    try:
        async with short_session(storage) as session:
            rows = list(
                await session.scalars(
                    select(MemoryRecordSourceRow).where(
                        MemoryRecordSourceRow.memory_id == memory_id,
                        MemoryRecordSourceRow.record_id.in_([item.id for item in items]),
                    )
                )
            )
            ids = {value for row in rows for value in (row.created_principal_id, row.last_write_principal_id) if value}
            names = {
                principal_id: name
                for principal_id, name in await session.execute(
                    select(PrincipalRow.id, PrincipalRow.name).where(PrincipalRow.id.in_(ids))
                )
            }
            by_id = {row.record_id: row for row in rows}
            for item in result:
                if (row := by_id.get(item.id)) is not None:
                    item.provenance = MemoryRecordProvenance(
                        created=_source(row, "created", names) if row.created_confirmed_at is not None else None,
                        last_write=_source(row, "last_write", names),
                        content_matches=row.content_hash == _digest(item.text),
                    )
    except SQLAlchemyError:
        logger.warning("Record sources could not be read", extra={"memory_id": memory_id})
    return result
