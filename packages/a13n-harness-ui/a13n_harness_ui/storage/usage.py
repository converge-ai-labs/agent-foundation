"""Durable, idempotent observed usage, independent of retained conversation history."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from a13n_harness import HarnessRunResultEvent
from a13n_harness.events import HarnessEvent, HarnessExtensionEvent, UsageReportPayload
from a13n_harness.usage import ModelUsageRecord, ProviderUsageRecord, UsageRecord
from anyio import Lock
from pydantic import Field, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_harness_ui.errors import StoreIntegrityError

from .database import DatabaseSessions, short_session, transaction
from .models import ThreadRecord, ThreadUsageRecord

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
            counters = usage.model_dump(exclude={"details", "cost"})
            for name in _COUNTERS:
                self.tokens[name] += counters[name]
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
    """Bounded process cache of a committed usage prefix, not an accounting authority."""

    recent_ids: tuple[str, ...]
    cursor: int = 0
    first: datetime | None = None
    last: datetime | None = None
    root: _Totals = field(default_factory=_Totals)
    children: _Totals = field(default_factory=_Totals)
    combined: _Totals = field(default_factory=_Totals)
    other: _Totals = field(default_factory=_Totals)
    other_runs: _Totals = field(default_factory=_Totals)
    models: dict[str, _Totals] = field(default_factory=dict)
    runs: dict[str, _Totals] = field(default_factory=dict)
    run_agents: dict[str, tuple[str, bool]] = field(default_factory=dict)
    model_owners: dict[tuple[str, bool], _Totals] = field(default_factory=dict)
    groups: dict[tuple[str, str, bool, str], _Totals] = field(default_factory=dict)
    other_groups: _Totals = field(default_factory=_Totals)

    def add(self, sequence: int, descendant: bool, payload: str, observed: datetime) -> None:
        record = _RECORD.validate_json(payload)
        self.first = observed if self.first is None else min(self.first, observed)
        self.last = observed if self.last is None else max(self.last, observed)
        self.combined.add(record)
        if record.run_id in self.recent_ids:
            self.runs.setdefault(record.run_id, _Totals()).add(record)
            self.run_agents[record.run_id] = (record.agent_instance_id, descendant)
        else:
            self.other_runs.add(record)
        (self.children if descendant else self.root).add(record)
        if isinstance(record, ModelUsageRecord):
            name = f"{record.provider_name or 'unknown'}/{record.model_name or 'unknown'}"
            if name in self.models or len(self.models) < _GROUPS:
                self.models.setdefault(name, _Totals()).add(record)
            else:
                self.other.add(record)
                name = "Other models"
            self.model_owners.setdefault((name, descendant), _Totals()).add(record)
            key = (name, record.agent_instance_id, descendant, record.source)
            if key in self.groups or len(self.groups) < _GROUPS * 4:
                self.groups.setdefault(key, _Totals()).add(record)
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
                for run_id in self.recent_ids
                if run_id in self.run_agents
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


class ThreadUsageRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions
        self._cache: OrderedDict[str, _Aggregation] = OrderedDict()
        self._snapshot_lock = Lock()

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
                records = tuple(_RECORD.validate_python(record) for record in report.records)
        elif isinstance(item, HarnessRunResultEvent):
            records = tuple(item.result.usage_records)
        for start in range(0, len(records), _BATCH):
            await self.append(thread_id=thread_id, records=records[start : start + _BATCH])

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
                if existing is not None:
                    prior = _RECORD.validate_json(existing.payload_json)
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
        """Read request-local usage already committed during execution, not Run totals."""
        query = select(ThreadUsageRecord.payload_json).where(
            ThreadUsageRecord.root_thread_id == thread_id,
            ThreadUsageRecord.descendant.is_(False),
            func.json_extract(ThreadUsageRecord.payload_json, "$.kind") == "model",
            func.coalesce(func.json_extract(ThreadUsageRecord.payload_json, "$.source"), "agent") == "agent",
        )
        if run_id is not None:
            query = query.where(ThreadUsageRecord.run_id == run_id)
        async with short_session(self._sessions) as session:
            payload = await session.scalar(query.order_by(ThreadUsageRecord.sequence.desc()).limit(1))
        return None if payload is None else ModelUsageRecord.model_validate_json(payload)

    async def snapshot(self, *, thread_id: str) -> ThreadUsageView:
        """Aggregate only a committed suffix while the bounded group membership is stable."""
        async with self._snapshot_lock:
            return await self._snapshot(thread_id=thread_id)

    async def _snapshot(self, *, thread_id: str) -> ThreadUsageView:
        async with short_session(self._sessions) as session:
            root_id = await self._root_id(session, thread_id)
            if root_id != thread_id:
                raise ValueError("Thread usage is available for root Threads.")
            high_water = (
                await session.scalar(
                    select(func.max(ThreadUsageRecord.sequence)).where(
                        ThreadUsageRecord.root_thread_id == root_id,
                    )
                )
                or 0
            )
            recent_ids = tuple(
                (
                    await session.scalars(
                        select(ThreadUsageRecord.run_id)
                        .where(
                            ThreadUsageRecord.root_thread_id == root_id,
                            ThreadUsageRecord.sequence <= high_water,
                        )
                        .group_by(ThreadUsageRecord.run_id)
                        .order_by(func.max(ThreadUsageRecord.sequence).desc())
                        .limit(_GROUPS)
                    )
                ).all()
            )
        aggregate = self._cache.pop(thread_id, None)
        if aggregate is None or aggregate.cursor > high_water or set(aggregate.recent_ids) != set(recent_ids):
            # A changed recent-Run window can change first-observed currency attribution
            # in 'other'; rebuild instead of subtracting lossy capped groups.
            aggregate = _Aggregation(recent_ids=recent_ids)
        aggregate.recent_ids = recent_ids
        while aggregate.cursor < high_water:
            async with short_session(self._sessions) as session:
                rows = (
                    await session.execute(
                        select(
                            ThreadUsageRecord.sequence,
                            ThreadUsageRecord.descendant,
                            ThreadUsageRecord.payload_json,
                            ThreadUsageRecord.observed_at,
                        )
                        .where(
                            ThreadUsageRecord.root_thread_id == root_id,
                            ThreadUsageRecord.sequence > aggregate.cursor,
                            ThreadUsageRecord.sequence <= high_water,
                        )
                        .order_by(ThreadUsageRecord.sequence)
                        .limit(_BATCH)
                    )
                ).all()
            if not rows:
                break
            for sequence, descendant, payload, observed in rows:
                aggregate.add(sequence, descendant, payload, observed)
        self._cache[thread_id] = aggregate
        while len(self._cache) > 16:
            self._cache.popitem(last=False)
        return aggregate.view(thread_id)

    @staticmethod
    async def _root_id(session: AsyncSession, thread_id: str) -> str:
        ancestors: set[str] = set()
        current = thread_id
        while current not in ancestors:
            ancestors.add(current)
            thread = await session.get(ThreadRecord, current)
            if thread is None:
                raise StoreIntegrityError("Usage refers to an unknown Thread.", code="thread_not_found")
            if thread.parent_thread_id is None:
                return current
            current = thread.parent_thread_id
        raise StoreIntegrityError("Thread ancestry contains a cycle.", code="thread_ancestry_invalid")
