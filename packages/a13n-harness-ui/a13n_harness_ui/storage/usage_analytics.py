"""Read-only model consumption analytics over the retained root-family ledger."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from .database import DatabaseSessions
from .models import ThreadConfigurationRecord, ThreadRecord, ThreadUsageRecord, UsageFactRecord
from .usage import _Totals, configured_usage_models
from .usage_index import configured_model, indexed_usage_session


class UsageQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: date
    end: date
    project_id: str | None = None
    projectless: bool = False
    thread_id: str | None = None
    model: str | None = None
    group_by: Literal["project", "thread", "model"] = "project"
    order_by: Literal["tokens", "cost"] = "tokens"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=25, ge=1, le=100)

    @model_validator(mode="after")
    def valid_scope(self) -> Self:
        if not 1 <= (self.end - self.start).days <= 366:
            raise ValueError("Choose a UTC date range of 1 to 366 days; end is exclusive.")
        if self.projectless and self.project_id is not None:
            raise ValueError("Project and projectless filters are mutually exclusive.")
        return self


class ConsumptionTotals(BaseModel):
    model_requests: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    model_cost_usd: str | None
    unknown_model_costs: int


def _view(totals: _Totals) -> ConsumptionTotals:
    return ConsumptionTotals(
        model_requests=totals.requests,
        input_tokens=totals.tokens["input_tokens"],
        output_tokens=totals.tokens["output_tokens"],
        total_tokens=totals.tokens["input_tokens"] + totals.tokens["output_tokens"],
        cache_read_tokens=totals.tokens["cache_read_tokens"],
        cache_write_tokens=totals.tokens["cache_write_tokens"],
        model_cost_usd=str(totals.cost) if totals.requests > totals.unknown else None,
        unknown_model_costs=totals.unknown,
    )


class UsageOption(BaseModel):
    key: str
    label: str


class UsageThreadOption(UsageOption):
    project_id: str | None


class UsageDay(BaseModel):
    day: date
    totals: ConsumptionTotals


class UsageGroup(BaseModel):
    key: str
    label: str
    project_id: str | None = None
    thread_id: str | None = None
    model: str | None = None
    totals: ConsumptionTotals


class UsageAnalyticsView(BaseModel):
    as_of: datetime
    start: date
    end: date
    first_observed_at: datetime | None
    observed_through: datetime | None
    summary: ConsumptionTotals
    daily: tuple[UsageDay, ...]
    groups: tuple[UsageGroup, ...]
    group_count: int
    projects: tuple[UsageOption, ...]
    threads: tuple[UsageThreadOption, ...]
    models: tuple[UsageOption, ...]


class UsageAnalyticsRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def snapshot(self, query: UsageQuery, *, project_names: Mapping[str, str]) -> UsageAnalyticsView:
        def project_label(project_id: str | None) -> str:
            if project_id is None:
                return "No project"
            return project_names.get(project_id, f"{project_id} (unavailable)")

        roots = (
            select(ThreadRecord.thread_id)
            .outerjoin(ThreadConfigurationRecord, ThreadConfigurationRecord.thread_id == ThreadRecord.thread_id)
            .where(ThreadRecord.parent_thread_id.is_(None))
        )
        if query.project_id is not None:
            roots = roots.where(ThreadConfigurationRecord.project_id == query.project_id)
        elif query.projectless:
            roots = roots.where(ThreadConfigurationRecord.project_id.is_(None))
        if query.thread_id is not None:
            roots = roots.where(ThreadRecord.thread_id == query.thread_id)

        totals = _Totals()
        days: dict[date, _Totals] = defaultdict(_Totals)
        groups: dict[str, _Totals] = defaultdict(_Totals)
        identities: dict[str, tuple[str, str | None, str | None, str | None]] = {}
        models: dict[str, str] = {}
        first: datetime | None = None
        last: datetime | None = None
        async with indexed_usage_session(self._sessions) as session:
            configured = {
                recipe: configured_model(name) for recipe, name in (await configured_usage_models(session)).items()
            }
            metadata = (
                await session.execute(
                    select(
                        ThreadRecord.thread_id,
                        ThreadRecord.title,
                        ThreadRecord.first_input,
                        ThreadRecord.memory_scope,
                        ThreadConfigurationRecord.project_id,
                        ThreadConfigurationRecord.agent_source_kind,
                    )
                    .outerjoin(ThreadConfigurationRecord, ThreadConfigurationRecord.thread_id == ThreadRecord.thread_id)
                    .where(ThreadRecord.parent_thread_id.is_(None))
                )
            ).all()
            as_of = datetime.now(UTC)
            threads: dict[str, UsageThreadOption] = {}
            project_ids = set(project_names)
            for thread_id, title, first_input, memory_scope, project_id, kind in metadata:
                if project_id is not None:
                    project_ids.add(project_id)
                label = title or first_input or thread_id
                if memory_scope is not None:
                    label = f"Memory · {project_label(project_id) if project_id else 'Global'}"
                elif kind == "live":
                    label = "Live"
                threads[thread_id] = UsageThreadOption(key=thread_id, label=label, project_id=project_id)

            f = UsageFactRecord
            rows = await session.stream(
                select(
                    f.root_thread_id,
                    f.day,
                    f.recipe_id,
                    f.model_key,
                    f.model_label,
                    f.input_tokens,
                    f.output_tokens,
                    f.cache_read_tokens,
                    f.cache_write_tokens,
                    f.cost,
                    ThreadUsageRecord.observed_at,
                    ThreadUsageRecord.indexed_through,
                )
                .join(ThreadUsageRecord, ThreadUsageRecord.sequence == f.sequence)
                .where(f.root_thread_id.in_(roots), f.day >= query.start, f.day < query.end)
            )
            async for (
                root_id,
                day,
                recipe,
                fallback_key,
                fallback_label,
                inputs,
                outputs,
                reads,
                writes,
                cost,
                observed,
                observed_through,
            ) in rows.yield_per(512):
                selected_model, model_label = configured.get(recipe, (fallback_key, fallback_label))
                models[selected_model] = model_label
                if query.model is not None and query.model != selected_model:
                    continue
                first = observed if first is None else min(first, observed)
                last = observed_through if last is None else max(last, observed_through)
                owner = threads[root_id]
                if query.group_by == "project":
                    key = owner.project_id or ""
                    identity = (project_label(owner.project_id), owner.project_id, None, None)
                elif query.group_by == "thread":
                    key = root_id
                    identity = (owner.label, owner.project_id, root_id, None)
                else:
                    key = selected_model
                    identity = (model_label, None, None, selected_model)
                identities[key] = identity
                for bucket in (totals, days[day], groups[key]):
                    bucket.requests += 1
                    bucket.tokens["input_tokens"] += inputs
                    bucket.tokens["output_tokens"] += outputs
                    bucket.tokens["cache_read_tokens"] += reads
                    bucket.tokens["cache_write_tokens"] += writes
                    if cost is None:
                        bucket.unknown += 1
                    else:
                        bucket.cost += Decimal(cost)

        def order(key: str) -> tuple[bool, Decimal | int, str]:
            value = groups[key]
            if query.order_by == "cost":
                return (value.requests == value.unknown, -value.cost, key)
            return (False, -(value.tokens["input_tokens"] + value.tokens["output_tokens"]), key)

        selected = sorted(groups, key=order)[query.offset : query.offset + query.limit]
        return UsageAnalyticsView(
            as_of=as_of,
            start=query.start,
            end=query.end,
            first_observed_at=first,
            observed_through=last,
            summary=_view(totals),
            daily=tuple(
                UsageDay(day=day, totals=_view(days.get(day, _Totals())))
                for index in range((query.end - query.start).days)
                for day in (query.start + timedelta(days=index),)
            ),
            groups=tuple(
                UsageGroup(
                    key=key,
                    label=identities[key][0],
                    project_id=identities[key][1],
                    thread_id=identities[key][2],
                    model=identities[key][3],
                    totals=_view(groups[key]),
                )
                for key in selected
            ),
            group_count=len(groups),
            projects=tuple(
                UsageOption(key=key, label=project_label(key))
                for key in sorted(project_ids, key=lambda key: (project_label(key).casefold(), key))
            ),
            threads=tuple(
                sorted(
                    (
                        owner
                        for owner in threads.values()
                        if (query.project_id is None or owner.project_id == query.project_id)
                        and (not query.projectless or owner.project_id is None)
                    ),
                    key=lambda owner: (owner.label.casefold(), owner.key),
                )
            ),
            models=tuple(UsageOption(key=key, label=label) for key, label in sorted(models.items())),
        )
