"""Rebuildable response-date index of winning contributions in the retained usage ledger."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Literal

from a13n_harness.usage import ModelUsageRecord, UsageRecord
from anyio.to_thread import run_sync
from pydantic import BaseModel, Field, TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .database import DatabaseSessions, short_session, transaction
from .models import ThreadUsageRecord, UsageFactRecord


class _SnapshotRecords(BaseModel):
    # Writes validate the complete UsageSnapshot, including its serialized size.
    # Reads need only the typed records, without reserializing the whole snapshot.
    records: tuple[UsageRecord, ...]


class _SnapshotEnvelope(BaseModel):
    kind: Literal["snapshot"]
    snapshot: _SnapshotRecords
    observed_through: datetime


_ENVELOPE = TypeAdapter(Annotated[_SnapshotEnvelope | UsageRecord, Field(discriminator="kind")])


def configured_model(name: str) -> tuple[str, str]:
    return json.dumps([name], separators=(",", ":")), name


def usage_model(record: ModelUsageRecord, configured: str | None) -> tuple[str, str]:
    """Stable filter key and visible label, including pre-selection observations."""
    if record.model_id:
        name = configured or record.model_id
        return configured_model(name)
    return json.dumps([record.provider_name, record.model_name], separators=(",", ":")), reported_model(record)


def reported_model(record: ModelUsageRecord) -> str:
    provider = record.provider_name or "unknown"
    model = record.model_name or "unknown"
    return model if model.startswith(f"{provider}/") else f"{provider}/{model}"


def fact(record: UsageRecord) -> dict[str, object]:
    model = record if isinstance(record, ModelUsageRecord) else None
    usage = model.request_usage if model else None
    key, label = usage_model(model, None) if model else (None, None)
    return dict(
        record_id=record.record_id,
        day=model.response_timestamp.astimezone(UTC).date() if model else None,
        recipe_id=model.model_id if model else None,
        model_key=key,
        model_label=label,
        input_tokens=usage.input_tokens if usage else 0,
        output_tokens=usage.output_tokens if usage else 0,
        cache_read_tokens=usage.cache_read_tokens if usage else 0,
        cache_write_tokens=usage.cache_write_tokens if usage else 0,
        cost=str(usage.cost) if usage and usage.cost is not None else None,
    )


async def index_facts(session: AsyncSession, sequence: int, root: str, facts: Sequence[dict[str, object]]) -> None:
    if not facts:
        return
    table = UsageFactRecord.metadata.tables[UsageFactRecord.__tablename__]
    stmt = insert(table)
    # A mutable earlier scope can acquire an ID after a later scope published it.
    stmt = stmt.on_conflict_do_update(
        index_elements=["root_thread_id", "record_id"],
        set_={c.name: stmt.excluded[c.name] for c in table.c if not c.primary_key},
        where=stmt.excluded.sequence <= table.c.sequence,
    )
    await session.execute(stmt, [dict(f, root_thread_id=root, sequence=sequence) for f in facts])


def _unpack(payload: str, observed: datetime) -> tuple[datetime, list[dict[str, object]]]:
    envelope = _ENVELOPE.validate_json(payload)
    if isinstance(envelope, _SnapshotEnvelope):
        return envelope.observed_through, [fact(r) for r in envelope.snapshot.records]
    return observed, [fact(envelope)]


async def backfill_usage_index(sessions: DatabaseSessions) -> None:
    # Commit one bounded envelope at a time. No writer or connection during decoding.
    ledger = ThreadUsageRecord
    pending = ledger.indexed_through.is_(None)
    while True:
        async with short_session(sessions) as session:
            row = (
                await session.execute(
                    select(ledger.sequence, ledger.root_thread_id, ledger.payload_json, ledger.observed_at)
                    .where(pending)
                    .order_by(ledger.sequence)
                    .limit(1)
                )
            ).first()
        if row is None:
            return
        sequence, root, payload, observed = row
        through, facts = await run_sync(_unpack, payload, observed)
        async with transaction(sessions) as session:
            # Skip when another reader backfilled or a writer replaced this scope.
            claimed = await session.scalar(
                update(ledger)
                .where(ledger.sequence == sequence, pending, ledger.payload_json == payload)
                .values(indexed_through=through)
                .returning(ledger.sequence)
            )
            if claimed is not None:
                await index_facts(session, sequence, root, facts)
