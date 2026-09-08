"""Durable, idempotent observed usage, independent of retained conversation history."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from a13n_harness import HarnessRunResultEvent
from a13n_harness.events import HarnessEvent, HarnessExtensionEvent, UsageReportPayload
from a13n_harness.usage import ModelUsageRecord, ProviderUsageRecord, UsageRecord
from pydantic import Field, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_harness_ui.errors import StoreIntegrityError

from .database import short_session, transaction
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


class ThreadUsageRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
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

    async def snapshot(self, *, thread_id: str) -> ThreadUsageView:
        """Read a finite high-water snapshot in detached batches with bounded aggregation memory."""
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
        runs = {run_id: _Totals() for run_id in recent_ids}
        run_agents: dict[str, tuple[str, bool]] = {}
        other_runs = _Totals()
        root, children, combined, other = _Totals(), _Totals(), _Totals(), _Totals()
        models: dict[str, _Totals] = {}
        first: datetime | None = None
        last: datetime | None = None
        cursor = 0
        while cursor < high_water:
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
                            ThreadUsageRecord.sequence > cursor,
                            ThreadUsageRecord.sequence <= high_water,
                        )
                        .order_by(ThreadUsageRecord.sequence)
                        .limit(_BATCH)
                    )
                ).all()
            if not rows:
                break
            for sequence, descendant, payload, observed in rows:
                cursor = sequence
                first = observed if first is None else min(first, observed)
                last = observed if last is None else max(last, observed)
                record = _RECORD.validate_json(payload)
                combined.add(record)
                if record.run_id in runs:
                    runs[record.run_id].add(record)
                    run_agents[record.run_id] = (record.agent_instance_id, descendant)
                else:
                    other_runs.add(record)
                (children if descendant else root).add(record)
                if isinstance(record, ModelUsageRecord):
                    name = f"{record.provider_name or 'unknown'}/{record.model_name or 'unknown'}"
                    if name in models or len(models) < _GROUPS:
                        models.setdefault(name, _Totals()).add(record)
                    else:
                        other.add(record)
        return ThreadUsageView(
            thread_id=thread_id,
            first_observed_at=first,
            observed_through=last,
            root=root.view(),
            descendants=children.view(),
            combined=combined.view(),
            models=tuple((name, totals.view()) for name, totals in sorted(models.items())),
            other_models=other.view(),
            recent_runs=tuple(
                RunUsageView(
                    run_id=run_id,
                    agent_instance_id=run_agents[run_id][0],
                    descendant=run_agents[run_id][1],
                    totals=runs[run_id].view(),
                )
                for run_id in recent_ids
                if run_id in run_agents
            ),
            other_runs=other_runs.view(),
        )

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
