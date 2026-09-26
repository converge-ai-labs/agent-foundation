"""Current Context accounting and legacy facts from known attempts, including late reports.

Ingestion is not fenced by the worker lease: it records a past charge, so an expired or finished attempt
may still report. It is scoped instead to the run's attempt and tenant. Records keep the Harness run that
made them: the attempt's own, or an inline subagent's whose events the attempt's stream forwards.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated

from a13n_harness.events import UsageReportPayload
from a13n_harness.usage import (
    ModelUsageRecord,
    ProviderUsageRecord,
    UsageRecord,
    UsageSnapshot,
    select_usage_snapshot,
    validate_contribution,
)
from a13n_logging import get_logger
from pydantic import Field, JsonValue, TypeAdapter
from sqlalchemy import BigInteger, ColumnElement, Numeric, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.models.service import ResolvedModel
from a13n_service.runs.schemas import ModelUsage, UsageFilter, UsageSummary, canonical_json
from a13n_service.runs.tables import AttemptRow, RunRow, UsageRecordRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal

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


async def ingest_snapshot(
    storage: Storage,
    run_id: str,
    attempt_id: str,
    snapshot: UsageSnapshot,
    calls: Mapping[str, ResolvedModel],
) -> None:
    """Replace one producer's latest state and its query projection in a short transaction.

    The attempt lock serializes reports, not execution authority. A sealed or fenced
    attempt may still deliver charges, but cannot move any execution checkpoint here.
    """
    async with transaction(storage) as session:
        # Match checkpoint lock order: inserts also acquire a Run foreign-key lock.
        # Taking Attempt first can deadlock with a checkpoint holding Run then waiting for Attempt.
        run = await session.get(RunRow, run_id, with_for_update=True)
        attempt = await session.scalar(select(AttemptRow).where(AttemptRow.id == attempt_id).with_for_update())
        if run is None or attempt is None or attempt.run_id != run.id:
            raise ServiceError("not_found", "Usage report names no matching attempt")
        scope = await session.get(UsageRecordRow, snapshot.usage_id)
        if scope is not None:
            if scope.run_id != run_id or scope.run_attempt_id != attempt_id:
                raise ServiceError("conflict", "Usage scope changed its attempt")
            previous = UsageSnapshot.model_validate(scope.record)
            selected = select_usage_snapshot(previous, snapshot)
            if selected.sequence == previous.sequence:
                return
        reports = []
        for record in snapshot.records:
            model = calls.get(record.call_id or "") if isinstance(record, ModelUsageRecord) else None
            reports.append(UsageReport(record, model.id if model else None, price_snapshot(model) if model else None))
        reports.sort(key=lambda report: report.record.record_id)
        # Normalized current contributions preserve existing indexed SQL summaries
        # and each contribution's first ingestion time. They are not revision history.
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
        payload = snapshot.model_dump(mode="json")
        digest = hashlib.sha256(canonical_json([payload, run.id, attempt.id, None, None])).hexdigest()
        if scope is None:
            session.add(
                UsageRecordRow(
                    id=snapshot.usage_id,
                    organization_id=run.organization_id,
                    workspace_id=run.workspace_id,
                    run_id=run.id,
                    run_attempt_id=attempt.id,
                    harness_run_id=snapshot.run_id,
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


class SnapshotReporter:
    """Attempt-owned delivery, inherited by inline children without combining their scopes."""

    def __init__(self, storage: Storage, run_id: str, attempt_id: str, buffer: UsageBuffer) -> None:
        from anyio import Lock

        self.storage, self.run_id, self.attempt_id, self.buffer = storage, run_id, attempt_id, buffer
        self._pending: dict[str, UsageSnapshot] = {}
        self._lock = Lock()

    async def report(self, snapshot: UsageSnapshot) -> None:
        previous = self._pending.get(snapshot.usage_id)
        self._pending[snapshot.usage_id] = (
            select_usage_snapshot(previous, snapshot) if previous is not None else snapshot.model_copy(deep=True)
        )
        await self.flush()

    async def flush(self) -> None:
        async with self._lock:
            for snapshot in tuple(self._pending.values()):
                await ingest_snapshot(self.storage, self.run_id, self.attempt_id, snapshot, self.buffer.calls)
                self.buffer.ingested_snapshot(snapshot)
                if self._pending.get(snapshot.usage_id) == snapshot:
                    del self._pending[snapshot.usage_id]


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

    def ingested_snapshot(self, snapshot: UsageSnapshot) -> None:
        delivered = {record.record_id for record in snapshot.records}
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


async def summarize(storage: Storage, actor: Principal, workspace_id: str, where: UsageFilter) -> UsageSummary:
    """Model usage recorded in the workspace, per model, including reports that arrived after a seal.

    Cost sums each record's own priced cost; records the Harness could not price count tokens only.
    """
    query = (
        select(
            UsageRecordRow.model_id,
            func.count(UsageRecordRow.id),
            _token_sum("input_tokens"),
            _token_sum("output_tokens"),
            _token_sum("cache_read_tokens"),
            _token_sum("cache_write_tokens"),
            func.sum(UsageRecordRow.record["request_usage"]["cost"].astext.cast(Numeric)),
        )
        .join(RunRow, RunRow.id == UsageRecordRow.run_id)
        .where(_MODEL_RECORDS)
        .group_by(UsageRecordRow.model_id)
        .order_by(UsageRecordRow.model_id)
    )
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        query = query.where(UsageRecordRow.workspace_id == scope.workspace_id)
        for column, value in (
            (RunRow.id, where.run_id),
            (RunRow.thread_id, where.thread_id),
            (RunRow.session_id, where.session_id),
        ):
            if value is not None:
                query = query.where(column == value)
        if where.ingested_after is not None:
            query = query.where(UsageRecordRow.ingested_at >= where.ingested_after)
        if where.ingested_before is not None:
            query = query.where(UsageRecordRow.ingested_at < where.ingested_before)
        rows = (await session.execute(query)).all()
    models = [
        ModelUsage(
            model_id=model_id,
            requests=requests,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cost=cost,
        )
        for model_id, requests, input_tokens, output_tokens, cache_read, cache_write, cost in rows
    ]
    return UsageSummary(models=models)
