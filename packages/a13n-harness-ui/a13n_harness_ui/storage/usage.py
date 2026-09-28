"""Durable, idempotent observed usage, independent of retained conversation history."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic
from typing import Annotated, Literal

from a13n_harness import HarnessRunResultEvent, HarnessState
from a13n_harness.events import HarnessEvent, HarnessExtensionEvent, UsageReportPayload
from a13n_harness.usage import (
    ModelUsageRecord,
    ProviderUsageRecord,
    UsageRecord,
    UsageSnapshot,
    select_usage_snapshot,
)
from a13n_logging import get_logger
from anyio import sleep, to_thread
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import DateTime, func, literal, select, text, true, type_coerce, union_all
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_harness_ui.errors import StoreIntegrityError

from .database import DatabaseSessions, short_session, transaction
from .models import ThreadRecord, ThreadUsageRecord

logger = get_logger(__name__)

_RECORD = TypeAdapter(Annotated[UsageRecord, Field(discriminator="kind")])
_BATCH = 128
_GROUPS = 32
_COUNTERS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "input_audio_tokens",
    "output_audio_tokens",
    "cache_audio_read_tokens",
)


class _StoredSnapshot(BaseModel):
    """Host observation time is not part of the portable accounting state."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["snapshot"] = "snapshot"
    snapshot: UsageSnapshot
    observed_through: datetime


def _prepare_snapshot_write(
    snapshot: UsageSnapshot, previous_payload: str | None
) -> tuple[str, datetime, dict[str, ProviderUsageRecord]] | None:
    """Validate and serialize detached values; never move the session to a worker."""
    previous = None if previous_payload is None else _StoredSnapshot.model_validate_json(previous_payload).snapshot
    if previous is not None and select_usage_snapshot(previous, snapshot).sequence == previous.sequence:
        return None
    # Retained receipts were checked on admission; selection forbids losing or changing them.
    known = {record.record_id for record in previous.records} if previous is not None else set()
    receipts = {
        record.record_id: record
        for record in snapshot.records
        if isinstance(record, ProviderUsageRecord) and record.record_id not in known
    }
    now = datetime.now(UTC)
    payload = _StoredSnapshot(snapshot=snapshot, observed_through=now).model_dump_json()
    return payload, now, receipts


@dataclass(frozen=True, slots=True)
class UsageTotals:
    model_requests: int
    provider_receipts: int
    tokens: tuple[tuple[str, int], ...]
    model_cost_usd: Decimal
    unknown_model_costs: int
    provider_costs: tuple[tuple[str, Decimal], ...]
    unknown_provider_costs: int
    omitted_currency_receipts: int


@dataclass(frozen=True, slots=True)
class RunUsageView:
    run_id: str
    agent_instance_id: str
    descendant: bool
    totals: UsageTotals


@dataclass(frozen=True, slots=True)
class ModelUsageView:
    name: str
    root: UsageTotals
    descendants: UsageTotals
    combined: UsageTotals


@dataclass(frozen=True, slots=True)
class ModelUsageGroup:
    model: str
    agent_instance_id: str
    descendant: bool
    source: str
    totals: UsageTotals


@dataclass(frozen=True, slots=True)
class ThreadUsageView:
    thread_id: str
    first_observed_at: datetime | None
    observed_through: datetime | None
    root: UsageTotals
    descendants: UsageTotals
    combined: UsageTotals
    models: tuple[tuple[str, UsageTotals], ...]
    other_models: UsageTotals
    recent_runs: tuple[RunUsageView, ...]
    other_runs: UsageTotals
    model_scopes: tuple[ModelUsageView, ...] = ()
    groups: tuple[ModelUsageGroup, ...] = ()
    other_groups: UsageTotals | None = None


@dataclass
class _Totals:
    requests: int = 0
    receipts: int = 0
    tokens: dict[str, int] = field(default_factory=lambda: dict.fromkeys(_COUNTERS, 0))
    cost: Decimal = Decimal(0)
    unknown: int = 0
    currencies: dict[str, Decimal] = field(default_factory=dict)
    unknown_provider: int = 0
    omitted_currency: int = 0

    def add(self, record: UsageRecord) -> None:
        if isinstance(record, ModelUsageRecord):
            self.requests += 1
            usage = record.request_usage
            self.tokens["input_tokens"] += usage.input_tokens
            self.tokens["output_tokens"] += usage.output_tokens
            self.tokens["cache_read_tokens"] += usage.cache_read_tokens
            self.tokens["cache_write_tokens"] += usage.cache_write_tokens
            self.tokens["input_audio_tokens"] += usage.input_audio_tokens
            self.tokens["output_audio_tokens"] += usage.output_audio_tokens
            self.tokens["cache_audio_read_tokens"] += usage.cache_audio_read_tokens
            if usage.cost is None:
                self.unknown += 1
            else:
                self.cost += usage.cost
        else:
            self.receipts += 1
            receipt = record.usage
            if receipt.cost is None or receipt.currency is None:
                self.unknown_provider += 1
            elif receipt.currency in self.currencies or len(self.currencies) < _GROUPS:
                self.currencies[receipt.currency] = self.currencies.get(receipt.currency, Decimal(0)) + receipt.cost
            else:
                self.omitted_currency += 1

    def view(self) -> UsageTotals:
        return UsageTotals(
            model_requests=self.requests,
            provider_receipts=self.receipts,
            tokens=tuple(self.tokens.items()),
            model_cost_usd=self.cost,
            unknown_model_costs=self.unknown,
            provider_costs=tuple(sorted(self.currencies.items())),
            unknown_provider_costs=self.unknown_provider,
            omitted_currency_receipts=self.omitted_currency,
        )


@dataclass
class _Aggregation:
    """Bounded display groups derived from one coherent database read."""

    recent_sequences: dict[str, int] = field(default_factory=dict)
    cursor: int = 0
    first: datetime | None = None
    last: datetime | None = None
    root: _Totals = field(default_factory=_Totals)
    children: _Totals = field(default_factory=_Totals)
    combined: _Totals = field(default_factory=_Totals)
    other: _Totals = field(default_factory=_Totals)
    other_runs: _Totals = field(default_factory=_Totals)
    models: dict[str, _Totals] = field(default_factory=lambda: defaultdict(_Totals))
    runs: dict[str, _Totals] = field(default_factory=lambda: defaultdict(_Totals))
    run_agents: dict[str, tuple[str, bool]] = field(default_factory=dict)
    model_owners: dict[tuple[str, bool], _Totals] = field(default_factory=lambda: defaultdict(_Totals))
    groups: dict[tuple[str, str, bool, str], _Totals] = field(default_factory=lambda: defaultdict(_Totals))
    other_groups: _Totals = field(default_factory=_Totals)

    def add(
        self,
        sequence: int,
        descendant: bool,
        payload: str,
        observed: datetime,
        observed_through: datetime,
        recent_sequence: int | None,
    ) -> None:
        record = _RECORD.validate_json(payload)
        observed = observed.replace(tzinfo=UTC)
        observed_through = observed_through.replace(tzinfo=UTC)
        self.first = observed if self.first is None else min(self.first, observed)
        self.last = observed_through if self.last is None else max(self.last, observed_through)
        self.combined.add(record)
        if recent_sequence is not None:
            self.recent_sequences[record.run_id] = recent_sequence
            self.runs[record.run_id].add(record)
            self.run_agents[record.run_id] = (record.agent_instance_id, descendant)
        else:
            self.other_runs.add(record)
        (self.children if descendant else self.root).add(record)
        if isinstance(record, ModelUsageRecord):
            name = f"{record.provider_name or 'unknown'}/{record.model_name or 'unknown'}"
            if name in self.models or len(self.models) < _GROUPS:
                self.models[name].add(record)
            else:
                self.other.add(record)
                name = "Other models"
            self.model_owners[name, descendant].add(record)
            key = (name, record.agent_instance_id, descendant, record.source)
            if key in self.groups or len(self.groups) < _GROUPS * 4:
                self.groups[key].add(record)
            else:
                self.other_groups.add(record)
        self.cursor = sequence

    def view(self, thread_id: str) -> ThreadUsageView:
        return ThreadUsageView(
            thread_id=thread_id,
            first_observed_at=self.first,
            observed_through=self.last,
            root=self.root.view(),
            descendants=self.children.view(),
            combined=self.combined.view(),
            models=tuple((name, totals.view()) for name, totals in sorted(self.models.items())),
            other_models=self.other.view(),
            recent_runs=tuple(
                RunUsageView(
                    run_id=run_id,
                    agent_instance_id=self.run_agents[run_id][0],
                    descendant=self.run_agents[run_id][1],
                    totals=self.runs[run_id].view(),
                )
                for run_id in sorted(self.recent_sequences, key=lambda key: (-self.recent_sequences[key], key))
            ),
            other_runs=self.other_runs.view(),
            model_scopes=tuple(
                ModelUsageView(
                    name=name,
                    root=self.model_owners.get((name, False), _Totals()).view(),
                    descendants=self.model_owners.get((name, True), _Totals()).view(),
                    combined=totals.view(),
                )
                for name, totals in sorted(
                    [*self.models.items(), *([("Other models", self.other)] if self.other.requests else [])]
                )
            ),
            groups=tuple(
                ModelUsageGroup(
                    model=name, agent_instance_id=agent, descendant=child, source=source, totals=totals.view()
                )
                for (name, agent, child, source), totals in sorted(self.groups.items())
            ),
            other_groups=self.other_groups.view(),
        )


def _contributions(root_id: str, *, root_only: bool = False):
    """Flatten current scopes and legacy facts, retaining the first receipt attribution."""
    # Extract envelope metadata once per scope, not once per contribution.
    # Without materialization SQLite flattens the CTE and reparses a complete
    # growing snapshot for each record, making these reads quadratic.
    scopes = (
        select(
            ThreadUsageRecord.sequence,
            ThreadUsageRecord.descendant,
            ThreadUsageRecord.payload_json,
            ThreadUsageRecord.observed_at,
            func.json_extract(ThreadUsageRecord.payload_json, "$.kind").label("kind"),
            type_coerce(
                func.json_extract(ThreadUsageRecord.payload_json, "$.observed_through"), DateTime(timezone=True)
            ).label("observed_through"),
        )
        .where(
            ThreadUsageRecord.root_thread_id == root_id,
            ThreadUsageRecord.descendant.is_(False) if root_only else true(),
        )
        .cte("usage_scopes")
        .prefix_with("MATERIALIZED")
    )
    table = scopes.c
    entries = func.json_each(table.payload_json, "$.snapshot.records").table_valued("key", "value")
    legacy = select(
        table.sequence,
        literal(0).label("ordinal"),
        table.descendant,
        table.payload_json.label("payload"),
        table.observed_at,
        table.observed_at.label("observed_through"),
    ).where(table.kind != "snapshot")
    current = (
        select(
            table.sequence,
            entries.c.key.label("ordinal"),
            table.descendant,
            entries.c.value.label("payload"),
            table.observed_at,
            table.observed_through,
        )
        .select_from(scopes)
        .join(entries, true())
        .where(table.kind == "snapshot")
    )
    facts = union_all(legacy, current).cte("usage_contributions")
    ranked = select(
        facts,
        func.row_number()
        .over(
            partition_by=func.json_extract(facts.c.payload, "$.record_id"),
            order_by=(facts.c.sequence, facts.c.ordinal),
        )
        .label("position"),
    ).cte("distinct_usage")
    return select(ranked).where(ranked.c.position == 1).subquery()


@dataclass(frozen=True)
class ThreadUsageReporter:
    repository: ThreadUsageRepository
    thread_id: str

    async def report(self, snapshot: UsageSnapshot) -> None:
        """Own transient storage retries; queueing is cancellable, admitted commits settle."""
        # Inline child scopes have their own Harness Thread, but belong to this Host root.
        started = monotonic()
        for attempt in range(1, 4):
            try:
                await self.repository.save(thread_id=self.thread_id, snapshot=snapshot)
            except (TimeoutError, OperationalError) as exc:
                if isinstance(exc, OperationalError) and not (
                    isinstance(exc.orig, sqlite3.OperationalError)
                    and getattr(exc.orig, "sqlite_errorcode", 0) & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
                ):
                    raise
                logger.warning(
                    "Usage persistence attempt timed out or was busy",
                    extra={
                        "thread_id": self.thread_id,
                        "usage_id": snapshot.usage_id,
                        "usage_sequence": snapshot.sequence,
                        "attempt": attempt,
                        "elapsed_seconds": monotonic() - started,
                        "retrying": attempt < 3,
                    },
                )
                if attempt == 3:
                    raise
                # save() has returned its transaction/connection before this wait.
                await sleep(0.1 * attempt)
            else:
                elapsed = monotonic() - started
                if elapsed >= 5:
                    logger.warning(
                        "Usage persistence completed slowly",
                        extra={
                            "thread_id": self.thread_id,
                            "usage_id": snapshot.usage_id,
                            "usage_sequence": snapshot.sequence,
                            "elapsed_seconds": elapsed,
                        },
                    )
                return


class ThreadUsageRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def observe(self, *, thread_id: str, item: object) -> None:
        """Consume canonical events, not lossy UI summaries or inclusive RunUsage."""
        records: tuple[UsageRecord, ...] = ()
        if isinstance(item, HarnessEvent) and isinstance(item.event, HarnessExtensionEvent):
            event = item.event
            if (
                event.kind == "usage"
                and isinstance(event.payload, dict)
                and event.payload.get("type") == "usage_report"
            ):
                report = UsageReportPayload.model_validate(event.payload)
                if report.usage_id is not None:
                    # Current snapshots arrive through the direct Reporter seam, not display chunks.
                    return
                records = tuple(_RECORD.validate_python(record) for record in report.records)
        elif isinstance(item, HarnessRunResultEvent):
            state = item.result.state
            snapshot = UsageSnapshot.from_state(state) if state is not None else None
            if snapshot is not None:
                await self.save(thread_id=thread_id, snapshot=snapshot)
                return
            records = tuple(item.result.usage_records)
        for start in range(0, len(records), _BATCH):
            await self.append(thread_id=thread_id, records=records[start : start + _BATCH])

    def reporter(self, thread_id: str) -> ThreadUsageReporter:
        return ThreadUsageReporter(self, thread_id)

    async def save(self, *, thread_id: str, snapshot: UsageSnapshot) -> None:
        """Replace one current JSON payload under SQLite's cross-process writer lock."""
        async with transaction(self._sessions) as session:
            root_id = await self._root_id(session, thread_id)
            row = await session.scalar(
                select(ThreadUsageRecord).where(
                    ThreadUsageRecord.root_thread_id == root_id,
                    ThreadUsageRecord.record_id == snapshot.usage_id,
                )
            )
            if row is not None and row.origin_thread_id != thread_id:
                raise StoreIntegrityError("Usage scope changed its Host owner.", code="usage_record_conflict")
            # Compare against the admitted row atomically, but keep ledger-sized CPU work
            # off the event loop. The transaction still owns cancellation through commit.
            prepared = await to_thread.run_sync(
                _prepare_snapshot_write, snapshot, None if row is None else row.payload_json
            )
            if prepared is None:
                return
            payload, now, receipts = prepared
            # A provider receipt may be returned by several independent scopes.
            # Check new IDs under the writer lock and charge their first owner only.
            if receipts:
                facts = _contributions(root_id)
                for start in range(0, len(receipts), _BATCH):
                    ids = tuple(receipts)[start : start + _BATCH]
                    payloads = await session.scalars(
                        select(facts.c.payload).where(
                            func.json_extract(facts.c.payload, "$.record_id").in_(ids),
                        )
                    )
                    for prior_payload in payloads:
                        prior = _RECORD.validate_json(prior_payload)
                        if not isinstance(prior, ProviderUsageRecord) or prior.usage != receipts[prior.record_id].usage:
                            raise StoreIntegrityError("A receipt changed its facts.", code="usage_record_conflict")
            if row is None:
                session.add(
                    ThreadUsageRecord(
                        root_thread_id=root_id,
                        origin_thread_id=thread_id,
                        record_id=snapshot.usage_id,
                        run_id=snapshot.run_id,
                        descendant=thread_id != root_id or snapshot.parent_agent_instance_id is not None,
                        payload_json=payload,
                        observed_at=now,
                    )
                )
            else:
                row.payload_json = payload

    async def restore(self, *, thread_id: str, state: HarnessState) -> HarnessState:
        """Overlay accounting ahead of the selected execution checkpoint before a true resume."""
        previous = UsageSnapshot.from_state(state)
        if previous is None:
            # Shipped checkpoints predate accounting state. Preserve execution recovery,
            # starting fresh accounting while leaving their legacy facts untouched.
            return state
        async with short_session(self._sessions) as session:
            root_id = await self._root_id(session, thread_id)
            payload = await session.scalar(
                select(ThreadUsageRecord.payload_json).where(
                    ThreadUsageRecord.root_thread_id == root_id,
                    ThreadUsageRecord.origin_thread_id == thread_id,
                    ThreadUsageRecord.record_id == previous.usage_id,
                )
            )
        return state if payload is None else _StoredSnapshot.model_validate_json(payload).snapshot.restore(state)

    async def append(self, *, thread_id: str, records: tuple[UsageRecord, ...]) -> None:
        if not records:
            return
        if len(records) > _BATCH:
            raise ValueError("Usage writes are limited to 128 records per transaction.")
        async with transaction(self._sessions) as session:
            root_id = await self._root_id(session, thread_id)
            for record in records:
                existing = await session.scalar(
                    select(ThreadUsageRecord).where(
                        ThreadUsageRecord.root_thread_id == root_id,
                        ThreadUsageRecord.record_id == record.record_id,
                    )
                )
                payload = existing.payload_json if existing is not None else None
                if payload is None:
                    # A no-state terminal may repeat a fact already delivered in a scope.
                    facts = _contributions(root_id)
                    payload = await session.scalar(
                        select(facts.c.payload).where(
                            func.json_extract(facts.c.payload, "$.record_id") == record.record_id,
                        )
                    )
                if payload is not None:
                    prior = _RECORD.validate_json(payload)
                    # Provider receipt identity is global to a family; attribution is first-observed.
                    same = prior == record
                    if isinstance(prior, ProviderUsageRecord) and isinstance(record, ProviderUsageRecord):
                        same = prior.usage == record.usage
                    if not same:
                        raise StoreIntegrityError(
                            "A usage identity was reused with different facts.", code="usage_record_conflict"
                        )
                    continue
                session.add(
                    ThreadUsageRecord(
                        root_thread_id=root_id,
                        origin_thread_id=thread_id,
                        record_id=record.record_id,
                        run_id=record.run_id,
                        descendant=thread_id != root_id or record.parent_agent_instance_id is not None,
                        payload_json=record.model_dump_json(),
                        observed_at=datetime.now(UTC),
                    )
                )
                await session.flush()

    async def latest_root_request(self, *, thread_id: str, run_id: str | None = None) -> ModelUsageRecord | None:
        """Read current primary-request occupancy, never cumulative Context usage."""
        facts = _contributions(thread_id, root_only=True)
        query = select(facts.c.payload).where(
            facts.c.descendant.is_(False),
            func.json_extract(facts.c.payload, "$.kind") == "model",
            func.coalesce(func.json_extract(facts.c.payload, "$.source"), "agent") == "agent",
        )
        if run_id is not None:
            query = query.where(func.json_extract(facts.c.payload, "$.run_id") == run_id)
        async with short_session(self._sessions) as session:
            payload = await session.scalar(query.order_by(facts.c.sequence.desc(), facts.c.ordinal.desc()).limit(1))
        return None if payload is None else ModelUsageRecord.model_validate_json(payload)

    async def snapshot(self, *, thread_id: str) -> ThreadUsageView:
        """Rebuild bounded groups in a pinned read transaction; no stale cross-process cache."""
        async with short_session(self._sessions) as session:
            # aiosqlite's legacy transaction mode does not BEGIN for SELECT statements.
            await session.execute(text("BEGIN"))
            root_id = await self._root_id(session, thread_id)
            if root_id != thread_id:
                raise ValueError("Thread usage is available for root Threads.")
            # Both recent-Run selection and totals consume the same de-duplicated
            # facts. Materialize once within this statement rather than flattening
            # and sorting the complete family again in a second query.
            facts = select(_contributions(root_id)).cte("selected_usage").prefix_with("MATERIALIZED")
            run_id = func.json_extract(facts.c.payload, "$.run_id")
            recent = (
                select(run_id.label("run_id"), func.max(facts.c.sequence).label("last_sequence"))
                .group_by(run_id)
                .order_by(func.max(facts.c.sequence).desc(), run_id)
                .limit(_GROUPS)
                .cte("recent_usage_runs")
            )
            aggregate = _Aggregation()
            rows = await session.stream(
                select(
                    facts.c.sequence,
                    facts.c.descendant,
                    facts.c.payload,
                    facts.c.observed_at,
                    facts.c.observed_through,
                    recent.c.last_sequence,
                )
                .outerjoin(recent, run_id == recent.c.run_id)
                .order_by(facts.c.sequence, facts.c.ordinal)
            )
            async for batch in rows.partitions(_BATCH):
                for sequence, descendant, payload, observed, observed_through, recent_sequence in batch:
                    aggregate.add(sequence, descendant, payload, observed, observed_through, recent_sequence)
            return aggregate.view(thread_id)

    @staticmethod
    async def _root_id(session: AsyncSession, thread_id: str) -> str:
        ancestors: set[str] = set()
        current = thread_id
        while current not in ancestors:
            ancestors.add(current)
            row = (
                await session.execute(select(ThreadRecord.parent_thread_id).where(ThreadRecord.thread_id == current))
            ).one_or_none()
            if row is None:
                raise StoreIntegrityError("Usage refers to an unknown Thread.", code="thread_not_found")
            if row.parent_thread_id is None:
                return current
            current = row.parent_thread_id
        raise StoreIntegrityError("Thread ancestry contains a cycle.", code="thread_ancestry_invalid")
