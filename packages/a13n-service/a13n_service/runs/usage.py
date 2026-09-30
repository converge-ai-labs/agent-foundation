"""Current Context contributions and legacy facts from known attempts, including late reports.

Ingestion is not fenced by the worker lease: it records a past charge, so an expired or finished attempt
may still report. It is scoped instead to the run's attempt and tenant. Records keep the Harness run that
made them: the attempt's own, or an inline subagent's whose events the attempt's stream forwards.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal

from a13n_harness.events import UsageReportPayload
from a13n_harness.usage import (
    ModelUsageRecord,
    ProviderUsageRecord,
    UsageDelta,
    UsageRecord,
    UsageScope,
    validate_contribution,
)
from a13n_logging import get_logger
from pydantic import Field, JsonValue, TypeAdapter
from sqlalchemy import BigInteger, ColumnElement, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.models.service import ResolvedModel
from a13n_service.runs.schemas import canonical_json
from a13n_service.runs.tables import AttemptRow, RunRow, UsageRecordRow

logger = get_logger(__name__)

MAX_RECORD_BYTES = 65536

_RECORD: TypeAdapter[UsageRecord] = TypeAdapter(Annotated[UsageRecord, Field(discriminator="kind")])


@dataclass(frozen=True, slots=True)
class UsageReport:
    """One Harness usage record with the dispatch identity the Service established before sending it."""

    record: ModelUsageRecord | ProviderUsageRecord
    model_id: str | None = None
    # None records an unknown price; displayed historical cost never re-reads current pricing.
    price_snapshot: dict[str, JsonValue] | None = None


def _values(run: RunRow, attempt: AttemptRow, report: UsageReport, record: dict) -> dict:
    digest = hashlib.sha256(
        canonical_json([record, run.id, attempt.id, report.model_id, report.price_snapshot])
    ).hexdigest()
    return {
        "id": report.record.record_id,
        "organization_id": run.organization_id,
        "workspace_id": run.workspace_id,
        "run_id": run.id,
        "run_attempt_id": attempt.id,
        "harness_run_id": report.record.run_id,
        "call_id": getattr(report.record, "call_id", None),
        "digest": digest,
        "record": record,
        "model_id": report.model_id,
        "price_snapshot": report.price_snapshot,
    }


async def ingest(session: AsyncSession, run: RunRow, attempt: AttemptRow, reports: Sequence[UsageReport]) -> None:
    """Insert each record once, all in one statement; a duplicate ID with equal content is a no-op.

    A record that cannot be stored as reported, over its byte limit or reusing an ID for different content, is
    an integrity error of that record alone: it is logged and skipped, never failing the commit that carries it.
    The stored fact is never overwritten.
    """
    if attempt.run_id != run.id:
        raise ServiceError("forbidden", "Usage report does not belong to this attempt")
    rows: dict[str, dict] = {}
    for report in reports:
        record = report.record.model_dump(mode="json")
        if len(canonical_json(record)) > MAX_RECORD_BYTES:
            logger.error(
                "Usage record exceeds its byte limit", extra={"run_id": run.id, "record_id": report.record.record_id}
            )
            continue
        rows[report.record.record_id] = _values(run, attempt, report, record)
    if not rows:
        return
    await session.execute(
        insert(UsageRecordRow).values(list(rows.values())).on_conflict_do_nothing(index_elements=["id"])
    )
    stored = dict(
        (await session.execute(select(UsageRecordRow.id, UsageRecordRow.digest).where(UsageRecordRow.id.in_(rows))))
        .tuples()
        .all()
    )
    for record_id, row in rows.items():
        if stored.get(record_id) != row["digest"]:
            logger.error("Usage record integrity conflict", extra={"run_id": run.id, "record_id": record_id})


async def ingest_late(storage: Storage, run_id: str, attempt_id: str, reports: Sequence[UsageReport]) -> None:
    """Usage left when an attempt ends without a checkpoint commit: recorded even if the lease is gone."""
    if not reports:
        return
    async with transaction(storage) as session:
        run = await session.get(RunRow, run_id)
        attempt = await session.get(AttemptRow, attempt_id)
        if run is None or attempt is None:
            raise ServiceError("not_found", "Usage report names no attempt")
        await ingest(session, run, attempt, reports)


class _UsageCursor(UsageScope):
    """Fixed-size durable progress; contribution rows hold the actual accounting facts."""

    kind: Literal["cursor"] = "cursor"


async def ingest_delta(
    storage: Storage,
    run_id: str,
    attempt_id: str,
    delta: UsageDelta,
    calls: Mapping[str, ResolvedModel],
) -> None:
    """Commit changed contributions and their scope progress in one short transaction.

    The producer retains unacknowledged changes. Overlapping delivery can follow an
    uncertain commit, but a gap cannot advance progress past missing contributions.
    """
    cursor = _UsageCursor(**delta.scope.model_dump())
    payload = cursor.model_dump(mode="json")
    digest = hashlib.sha256(canonical_json([delta.model_dump(mode="json"), run_id, attempt_id])).hexdigest()
    reports = []
    for record in sorted(delta.records, key=lambda record: record.record_id):
        model = calls.get(record.call_id or "") if isinstance(record, ModelUsageRecord) else None
        reports.append(UsageReport(record, model.id if model else None, price_snapshot(model) if model else None))
    async with transaction(storage) as session:
        # Keep checkpoint lock order. Late charges are deliberately not lease-fenced.
        run = await session.get(RunRow, run_id, with_for_update=True)
        attempt = await session.scalar(select(AttemptRow).where(AttemptRow.id == attempt_id).with_for_update())
        if run is None or attempt is None or attempt.run_id != run.id:
            raise ServiceError("not_found", "Usage report names no matching attempt")
        scope = await session.get(UsageRecordRow, cursor.usage_id)
        previous_sequence = 0
        if scope is not None:
            if scope.run_id != run_id or scope.run_attempt_id != attempt_id or scope.record.get("kind") != "cursor":
                raise ServiceError("conflict", "Usage scope changed its owner or reporting contract")
            previous = _UsageCursor.model_validate(scope.record)
            progress = {"sequence", "tool_calls"}
            if previous.model_dump(exclude=progress) != cursor.model_dump(exclude=progress):
                raise ServiceError("conflict", "Usage scope changed its owner")
            previous_sequence = previous.sequence
            if cursor.sequence < previous_sequence:
                return
            if cursor.sequence == previous_sequence:
                if scope.digest != digest:
                    raise ServiceError("conflict", "Usage sequence has conflicting facts")
                return
            if cursor.tool_calls < previous.tool_calls:
                raise ServiceError("conflict", "Usage scope lost observed tool calls")
        if delta.after_sequence > previous_sequence:
            raise ServiceError("conflict", "Usage delivery skipped unacknowledged changes")
        if scope is None:
            session.add(
                UsageRecordRow(
                    id=cursor.usage_id,
                    organization_id=run.organization_id,
                    workspace_id=run.workspace_id,
                    run_id=run.id,
                    run_attempt_id=attempt.id,
                    harness_run_id=cursor.run_id,
                    call_id=None,
                    record=payload,
                    digest=digest,
                    model_id=None,
                    price_snapshot=None,
                )
            )
        else:
            scope.record = payload
            scope.digest = digest
        # Make the scope visible to guard_usage inside this transaction. A later
        # failure rolls this back together with every contribution mutation.
        await session.flush()
        for start in range(0, len(reports), 128):
            rows = {
                report.record.record_id: _values(run, attempt, report, report.record.model_dump(mode="json"))
                for report in reports[start : start + 128]
            }
            await session.execute(insert(UsageRecordRow).values(list(rows.values())).on_conflict_do_nothing())
            stored = await session.scalars(select(UsageRecordRow).where(UsageRecordRow.id.in_(rows)))
            for row in stored:
                candidate = rows[row.id]
                record = _RECORD.validate_python(candidate["record"])
                prior = _RECORD.validate_python(row.record)
                if isinstance(record, ProviderUsageRecord) and isinstance(prior, ProviderUsageRecord):
                    # Stable receipts retain their first Service owner, across attempts and Runs.
                    if row.workspace_id != run.workspace_id or prior.usage != record.usage:
                        raise ServiceError("conflict", "Provider receipt changed its facts or workspace")
                    continue
                if (
                    row.run_attempt_id != attempt_id
                    or row.model_id != candidate["model_id"]
                    or row.price_snapshot != candidate["price_snapshot"]
                ):
                    raise ServiceError("conflict", "Usage contribution changed its dispatch attribution")
                validate_contribution(prior, record)
                if row.record != candidate["record"]:
                    row.record = candidate["record"]
                    row.digest = candidate["digest"]


class DeltaReporter:
    """Attempt-owned delivery of unacknowledged batches, inherited by inline children."""

    def __init__(self, storage: Storage, run_id: str, attempt_id: str, buffer: UsageBuffer) -> None:
        from anyio import Lock

        self.storage, self.run_id, self.attempt_id, self.buffer = storage, run_id, attempt_id, buffer
        self._pending: dict[tuple[str, int], UsageDelta] = {}
        self._lock = Lock()

    async def report_delta(self, delta: UsageDelta) -> None:
        key = (delta.scope.usage_id, delta.scope.sequence)
        previous = self._pending.get(key)
        if previous is not None and previous != delta:
            raise ServiceError("conflict", "Usage sequence has conflicting pending facts")
        self._pending[key] = delta.model_copy(deep=True)
        await self.flush()

    async def flush(self) -> None:
        async with self._lock:
            for key, delta in tuple(self._pending.items()):
                await ingest_delta(self.storage, self.run_id, self.attempt_id, delta, self.buffer.calls)
                self.buffer.ingested_records(delta.records)
                del self._pending[key]


def price_snapshot(model: ResolvedModel) -> dict[str, JsonValue] | None:
    return model.pricing.model_dump(mode="json") if model.pricing is not None else None


class UsageBuffer:
    """One attempt's usage records not yet ingested, once each across usage events and the final result.

    A model record carries the identity and price of the model its call selected: `calls` maps the call IDs the
    call check admitted to their models.
    """

    def __init__(self, calls: Mapping[str, ResolvedModel]):
        self.calls = calls
        self.seen: set[str] = set()
        self._pending: list[UsageReport] = []

    def report(self, payload: JsonValue) -> None:
        """The payload of a `usage` extension event."""
        report = UsageReportPayload.model_validate(payload)
        if report.usage_id is None:
            self.add(_RECORD.validate_python(record) for record in report.records)

    def add(self, records: Iterable[UsageRecord]) -> None:
        for record in records:
            if record.record_id in self.seen:
                continue
            self.seen.add(record.record_id)
            if isinstance(record, ModelUsageRecord):
                # A charge is recorded even if its model cannot be told: unattributed, with an unknown price.
                model = self.calls.get(record.call_id) if record.call_id is not None else None
                model_id, price = (model.id, price_snapshot(model)) if model is not None else (None, None)
                self._pending.append(UsageReport(record, model_id, price))
            else:
                self._pending.append(UsageReport(record))

    def pending(self) -> tuple[UsageReport, ...]:
        """Reports not yet ingested, oldest first."""
        return tuple(self._pending)

    def ingested(self, reports: Sequence[UsageReport]) -> None:
        """Forget `reports`, which `pending` returned; reports added since then stay pending."""
        delivered = {report.record.record_id for report in reports}
        self._pending = [report for report in self._pending if report.record.record_id not in delivered]

    def ingested_records(self, records: Iterable[UsageRecord]) -> None:
        delivered = {record.record_id for record in records}
        self.seen.update(delivered)
        self._pending = [report for report in self._pending if report.record.record_id not in delivered]


def _token_sum(name: str) -> ColumnElement[int]:
    return func.coalesce(func.sum(UsageRecordRow.record["request_usage"][name].astext.cast(BigInteger)), 0)


_MODEL_RECORDS = UsageRecordRow.record["kind"].astext == "model"


async def totals(session: AsyncSession, run_id: str) -> dict[str, int]:
    """Model request count and tokens recorded so far; `requests` is what `max_usage` limits."""
    requests, input_tokens, output_tokens = (
        await session.execute(
            select(func.count(UsageRecordRow.id), _token_sum("input_tokens"), _token_sum("output_tokens")).where(
                UsageRecordRow.run_id == run_id, _MODEL_RECORDS
            )
        )
    ).one()
    return {"requests": int(requests), "input_tokens": int(input_tokens), "output_tokens": int(output_tokens)}
