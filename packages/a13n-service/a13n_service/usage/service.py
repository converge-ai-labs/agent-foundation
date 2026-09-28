"""Query current model contributions and Run lifetimes without precomputed accounting.

Consumption follows first ingestion, Runs follow their first start. Aggregate those two
populations independently before joining: a Run with many requests still counts once.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import BigInteger, ColumnElement, Numeric, RowMapping, Select, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.agents.tables import AgentRow
from a13n_service.resources.models.tables import ModelRow
from a13n_service.runs.tables import RunRow, UsageRecordRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal
from a13n_service.usage.schemas import (
    AgentUsage,
    AgentUsagePage,
    BreakdownQuery,
    DailyUsage,
    ModelMetrics,
    ModelUsage,
    ModelUsageGroup,
    ModelUsagePage,
    OverviewQuery,
    RunMetrics,
    UsageFilter,
    UsageOverview,
    UsageSummary,
    UsageWindow,
)


def _model_totals() -> Select:
    usage = UsageRecordRow.record["request_usage"]
    cost = usage["cost"].astext.cast(Numeric)
    return select(
        func.count().label("requests"),
        *(
            func.coalesce(func.sum(usage[name].astext.cast(BigInteger)), 0).label(name)
            for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
        ),
        func.sum(cost).label("cost"),
        func.count().filter(cost.is_(None)).label("unpriced_requests"),
    ).where(UsageRecordRow.record["kind"].astext == "model")


def _model_query(workspace_id: str, window: UsageWindow) -> Select:
    return _model_totals().where(
        UsageRecordRow.workspace_id == workspace_id,
        UsageRecordRow.ingested_at >= window.start,
        UsageRecordRow.ingested_at < window.end,
    )


def _run_query(workspace_id: str, window: UsageWindow) -> Select:
    return select(
        func.count().label("runs"),
        func.avg(func.extract("epoch", RunRow.sealed_at - RunRow.started_at)).label("average_duration_seconds"),
    ).where(
        RunRow.workspace_id == workspace_id,
        RunRow.started_at >= window.start,
        RunRow.started_at < window.end,
    )


def _metrics(row: RowMapping) -> ModelMetrics:
    requests = row["requests"] or 0
    input_tokens = row["input_tokens"] or 0
    cached = row["cache_read_tokens"] or 0
    return ModelMetrics(
        requests=requests,
        input_tokens=input_tokens,
        output_tokens=row["output_tokens"] or 0,
        cache_read_tokens=cached,
        cache_hit_rate=cached / input_tokens if input_tokens else None,
        cost=row["cost"] if requests else Decimal(0),
        unpriced_requests=row["unpriced_requests"] or 0,
    )


def _runs(row: RowMapping) -> RunMetrics:
    return RunMetrics(runs=row["runs"] or 0, average_duration_seconds=row["average_duration_seconds"])


async def overview(storage: Storage, actor: Principal, workspace_id: str, query: OverviewQuery) -> UsageOverview:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        # One statement computes totals and daily buckets from the same observations.
        day = func.date(func.timezone(query.timezone, UsageRecordRow.ingested_at))
        statement = _model_query(scope.workspace_id, query).add_columns(day.label("day"))
        rows = (await session.execute(statement.group_by(func.rollup(day)))).mappings().all()
        runs = (await session.execute(_run_query(scope.workspace_id, query))).mappings().one()
    totals = next(row for row in rows if row["day"] is None)
    days = {row["day"]: _metrics(row) for row in rows if row["day"] is not None}
    empty = ModelMetrics(
        requests=0,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_hit_rate=None,
        cost=Decimal(0),
        unpriced_requests=0,
    )
    zone = ZoneInfo(query.timezone)
    first = query.start.astimezone(zone).date()
    last = (query.end - timedelta(microseconds=1)).astimezone(zone).date()
    return UsageOverview(
        usage=_metrics(totals),
        runs=_runs(runs),
        daily=[
            DailyUsage(date=day, usage=days.get(day, empty))
            for offset in range((last - first).days + 1)
            for day in (first + timedelta(days=offset),)
        ],
    )


async def _page(
    session: AsyncSession,
    statement: Select,
    cost: ColumnElement,
    key: ColumnElement,
    workspace_id: str,
    query: BreakdownQuery,
    kind: str,
) -> tuple[list[RowMapping], str | None]:
    """Known cost descending, identity ascending; -1 sorts unknown cost after known zero."""
    order_cost = func.coalesce(cost, -1)
    owner = cursors.query_owner(workspace_id, query.start, query.end)
    if query.cursor is not None:
        position = cursors.decode(query.cursor, kind, owner)
        try:
            if len(position) != 2 or not all(isinstance(item, str) for item in position):
                raise ValueError("position")
            value = Decimal(str(position[0]))
            if not value.is_finite() or (value < 0 and value != -1) or len(str(position[1])) > 128:
                raise ValueError("position")
        except (ValueError, InvalidOperation):
            raise ServiceError("invalid_cursor", "Invalid collection cursor") from None
        statement = statement.where(or_(order_cost < value, (order_cost == value) & (key > position[1])))
    rows = (
        (
            await session.execute(
                statement.add_columns(order_cost.label("sort_cost"), key.label("sort_key"))
                .order_by(order_cost.desc(), key)
                .limit(query.limit + 1)
            )
        )
        .mappings()
        .all()
    )
    page = list(rows[: query.limit])
    next_cursor = (
        cursors.encode(kind, owner, str(page[-1]["sort_cost"]), page[-1]["sort_key"])
        if len(rows) > query.limit
        else None
    )
    return page, next_cursor


async def agents(storage: Storage, actor: Principal, workspace_id: str, query: BreakdownQuery) -> AgentUsagePage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        usage = (
            _model_query(scope.workspace_id, query)
            .join(RunRow, RunRow.id == UsageRecordRow.run_id)
            .add_columns(RunRow.agent_id)
            .group_by(RunRow.agent_id)
            .subquery()
        )
        runs = _run_query(scope.workspace_id, query).add_columns(RunRow.agent_id).group_by(RunRow.agent_id).subquery()
        # Include agents whose Runs have no model observations and late consumption from older Runs.
        key = func.coalesce(usage.c.agent_id, runs.c.agent_id)
        cost = case((func.coalesce(usage.c.requests, 0) == 0, 0), else_=usage.c.cost)
        statement = (
            select(
                AgentRow.id.label("agent_id"),
                AgentRow.name,
                usage.c.requests,
                usage.c.input_tokens,
                usage.c.output_tokens,
                usage.c.cache_read_tokens,
                cost.label("cost"),
                usage.c.unpriced_requests,
                runs.c.runs,
                runs.c.average_duration_seconds,
            )
            .select_from(usage.join(runs, usage.c.agent_id == runs.c.agent_id, full=True))
            .join(AgentRow, AgentRow.id == key)
        )
        rows, cursor = await _page(session, statement, cost, key, scope.workspace_id, query, "usage-agents")
    return AgentUsagePage(
        items=[
            AgentUsage(agent_id=row["agent_id"], name=row["name"], usage=_metrics(row), runs=_runs(row)) for row in rows
        ],
        next_cursor=cursor,
    )


async def models(storage: Storage, actor: Principal, workspace_id: str, query: BreakdownQuery) -> ModelUsagePage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        usage = (
            _model_query(scope.workspace_id, query)
            .add_columns(UsageRecordRow.model_id)
            .group_by(UsageRecordRow.model_id)
            .subquery()
        )
        statement = select(usage, ModelRow.key.label("model"), ModelRow.name).outerjoin(
            ModelRow, ModelRow.id == usage.c.model_id
        )
        rows, cursor = await _page(
            session, statement, usage.c.cost, func.coalesce(ModelRow.key, ""), scope.workspace_id, query, "usage-models"
        )
    return ModelUsagePage(
        items=[ModelUsageGroup(model=row["model"], name=row["name"], usage=_metrics(row)) for row in rows],
        next_cursor=cursor,
    )


async def summarize(storage: Storage, actor: Principal, workspace_id: str, where: UsageFilter) -> UsageSummary:
    """Model usage recorded in the workspace, per model, including reports that arrived after a seal.

    Cost sums each record's own priced cost; records the Harness could not price count tokens only.
    """
    query = (
        _model_totals()
        .add_columns(ModelRow.key.label("model"))
        .join(RunRow, RunRow.id == UsageRecordRow.run_id)
        .outerjoin(ModelRow, ModelRow.id == UsageRecordRow.model_id)
        .group_by(ModelRow.key)
        .order_by(ModelRow.key)
    )
    async with short_session(storage) as session:
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
        rows = (await session.execute(query)).mappings().all()
    return UsageSummary(models=[ModelUsage.model_validate(row) for row in rows])
