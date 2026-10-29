"""Query current model contributions and Run lifetimes without precomputed accounting.

Consumption follows first ingestion, Runs follow their first start. Aggregate those two
populations independently before joining: a Run with many requests still counts once.
"""

from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import (
    BigInteger,
    ColumnElement,
    DateTime,
    RowMapping,
    Select,
    and_,
    case,
    func,
    or_,
    select,
    tuple_,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import ServiceError, not_found
from a13n_service.resources.agents.tables import AgentRow
from a13n_service.resources.models.tables import ModelRow
from a13n_service.runs.tables import RunRow, UsageRecordRow
from a13n_service.runs.usage import model_cost
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.tables import PrincipalRow
from a13n_service.usage.schemas import (
    AgentUsage,
    AgentUsagePage,
    BreakdownQuery,
    DailyUsage,
    MemberUsage,
    MemberUsagePage,
    ModelMetrics,
    ModelRequestMetrics,
    ModelUsage,
    ModelUsageGroup,
    ModelUsagePage,
    OverviewQuery,
    RunMetrics,
    RunUsage,
    RunUsagePage,
    RunUsageQuery,
    UsageFilter,
    UsageOverview,
    UsageSummary,
    UsageWindow,
)


def _model_totals() -> Select:
    usage = UsageRecordRow.record["request_usage"]
    cost = model_cost()
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
    statement = _model_totals().where(
        UsageRecordRow.workspace_id == workspace_id,
        UsageRecordRow.ingested_at >= window.start,
        UsageRecordRow.ingested_at < window.end,
    )

    if window.principal_id is not None:
        statement = statement.where(
            select(RunRow.id)
            .where(RunRow.id == UsageRecordRow.run_id, RunRow.principal_id == window.principal_id)
            .correlate(UsageRecordRow)
            .exists()
        )
    return statement


def _run_query(workspace_id: str, window: UsageWindow) -> Select:
    statement = select(
        func.count().label("runs"),
        func.avg(func.extract("epoch", RunRow.sealed_at - RunRow.started_at)).label("average_duration_seconds"),
    ).where(
        RunRow.workspace_id == workspace_id,
        RunRow.started_at >= window.start,
        RunRow.started_at < window.end,
    )

    if window.principal_id is not None:
        statement = statement.where(RunRow.principal_id == window.principal_id)
    return statement


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
        scope = await workspace_scope(
            session, actor, workspace_id, "admin" if query.principal_id is not None else "read"
        )
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
    if query.principal_id is not None:
        owner = cursors.query_owner(owner, query.principal_id)
    if query.cursor is not None:
        position = cursors.decode(query.cursor, kind, owner)
        try:
            if len(position) != 2 or not all(isinstance(item, str) for item in position):
                raise ValueError("position")
            value = cursors.numeric_value(position[0])
            if (value < 0 and value != -1) or len(str(position[1])) > 128:
                raise ValueError("position")
        except ValueError:
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


def _group_totals(workspace_id: str, query: BreakdownQuery, identity: InstrumentedAttribute[str]) -> Select:
    usage = (
        _model_query(workspace_id, query)
        .join(RunRow, RunRow.id == UsageRecordRow.run_id)
        .add_columns(identity.label("identity_id"))
        .group_by(identity)
        .subquery()
    )
    runs = _run_query(workspace_id, query).add_columns(identity.label("identity_id")).group_by(identity).subquery()
    # Preserve Run-only identities and late consumption from Runs outside the window.
    key = func.coalesce(usage.c.identity_id, runs.c.identity_id)
    cost = case((func.coalesce(usage.c.requests, 0) == 0, 0), else_=usage.c.cost)
    return select(
        key.label("identity_id"),
        usage.c.requests,
        usage.c.input_tokens,
        usage.c.output_tokens,
        usage.c.cache_read_tokens,
        cost.label("cost"),
        usage.c.unpriced_requests,
        runs.c.runs,
        runs.c.average_duration_seconds,
    ).select_from(usage.join(runs, usage.c.identity_id == runs.c.identity_id, full=True))


async def agents(storage: Storage, actor: Principal, workspace_id: str, query: BreakdownQuery) -> AgentUsagePage:
    async with short_session(storage) as session:
        scope = await workspace_scope(
            session, actor, workspace_id, "admin" if query.principal_id is not None else "read"
        )
        totals = _group_totals(scope.workspace_id, query, RunRow.agent_id).subquery()
        statement = select(totals, AgentRow.id.label("agent_id"), AgentRow.name).join(
            AgentRow, AgentRow.id == totals.c.identity_id
        )
        rows, cursor = await _page(
            session, statement, totals.c.cost, totals.c.identity_id, scope.workspace_id, query, "usage-agents"
        )
    return AgentUsagePage(
        items=[
            AgentUsage(agent_id=row["agent_id"], name=row["name"], usage=_metrics(row), runs=_runs(row)) for row in rows
        ],
        next_cursor=cursor,
    )


async def members(storage: Storage, actor: Principal, workspace_id: str, query: BreakdownQuery) -> MemberUsagePage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        totals = _group_totals(scope.workspace_id, query, RunRow.principal_id).subquery()
        # Join only identities with facts in this workspace, never the global principal directory.
        statement = select(totals, PrincipalRow.name, PrincipalRow.kind, PrincipalRow.status).join(
            PrincipalRow, PrincipalRow.id == totals.c.identity_id
        )
        rows, cursor = await _page(
            session, statement, totals.c.cost, totals.c.identity_id, scope.workspace_id, query, "usage-members"
        )
    return MemberUsagePage(
        items=[
            MemberUsage(
                principal_id=row["identity_id"],
                name=row["name"],
                kind=row["kind"],
                status=row["status"],
                usage=_metrics(row),
                runs=_runs(row),
            )
            for row in rows
        ],
        next_cursor=cursor,
    )


async def models(storage: Storage, actor: Principal, workspace_id: str, query: BreakdownQuery) -> ModelUsagePage:
    async with short_session(storage) as session:
        scope = await workspace_scope(
            session, actor, workspace_id, "admin" if query.principal_id is not None else "read"
        )
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


def _timing_metrics() -> dict[str, ColumnElement]:
    """One timing policy for a single contribution and weighted model aggregates."""
    record = UsageRecordRow.record
    started, first_chunk, ended = (
        record[name].astext.cast(DateTime(timezone=True))
        for name in ("request_started_at", "first_chunk_at", "request_ended_at")
    )
    completed = record["outcome"].astext == "completed"
    streamed = and_(completed, first_chunk < ended)
    timed = and_(completed, started < ended)
    elapsed = func.sum(func.extract("epoch", ended - started)).filter(timed)
    return {
        "time_to_first_chunk_seconds": func.avg(func.extract("epoch", first_chunk - started)).filter(streamed),
        "request_duration_seconds": elapsed,
        "output_tokens_per_second": (
            func.sum(record["request_usage"]["output_tokens"].astext.cast(BigInteger)).filter(timed) / elapsed
        ),
    }


async def request_metrics(storage: Storage, actor: Principal, workspace_id: str, record_id: str) -> ModelRequestMetrics:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = (
            (
                await session.execute(
                    select(*(value.label(name) for name, value in _timing_metrics().items()))
                    .where(
                        UsageRecordRow.workspace_id == scope.workspace_id,
                        UsageRecordRow.id == record_id,
                        UsageRecordRow.record["kind"].astext == "model",
                    )
                    .group_by(UsageRecordRow.id)
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise not_found("usage_record", record_id)
    return ModelRequestMetrics.model_validate(row)


def _scoped_usage(workspace_id: str, where: UsageFilter) -> Select:
    timing = _timing_metrics()
    query = (
        _model_totals()
        .add_columns(
            timing["time_to_first_chunk_seconds"].label("average_time_to_first_chunk_seconds"),
            timing["output_tokens_per_second"].label("output_tokens_per_second"),
            ModelRow.key.label("model"),
        )
        .join(RunRow, RunRow.id == UsageRecordRow.run_id)
        .outerjoin(ModelRow, ModelRow.id == UsageRecordRow.model_id)
        .where(UsageRecordRow.workspace_id == workspace_id)
    )
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
    return query


def _run_numbers(workspace_id: str, where: UsageFilter) -> Select:
    # Number complete Threads before filtering usage or selecting a page.
    query = select(
        RunRow.id,
        func.row_number().over(partition_by=RunRow.thread_id, order_by=(RunRow.created_at, RunRow.id)).label("number"),
    ).where(RunRow.workspace_id == workspace_id)
    for column, value in ((RunRow.thread_id, where.thread_id), (RunRow.session_id, where.session_id)):
        if value is not None:
            query = query.where(column == value)
    if where.run_id is not None:
        query = query.where(
            RunRow.thread_id.in_(
                select(RunRow.thread_id).where(RunRow.workspace_id == workspace_id, RunRow.id == where.run_id)
            )
        )
    return query


def _run_usage_rows(rows: list[RowMapping]) -> list[RunUsage]:
    runs: dict[tuple[str, str, int], list[ModelUsage]] = {}
    for row in rows:
        runs.setdefault((row["run_id"], row["thread_id"], row["run_number"]), []).append(ModelUsage.model_validate(row))
    return [
        RunUsage(run_id=run, thread_id=thread, run_number=number, models=models)
        for (run, thread, number), models in runs.items()
    ]


async def summarize(storage: Storage, actor: Principal, workspace_id: str, where: UsageFilter) -> UsageSummary:
    """Current per-model totals, independent of the paginated Run breakdown."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        query = _scoped_usage(scope.workspace_id, where).group_by(ModelRow.key).order_by(ModelRow.key)
        rows = (await session.execute(query)).mappings().all()
    return UsageSummary(models=[ModelUsage.model_validate(row) for row in rows])


async def run_usage(storage: Storage, actor: Principal, workspace_id: str, where: RunUsageQuery) -> RunUsagePage:
    """Page whole Runs before aggregating their models, in one database snapshot."""
    owner = cursors.query_owner(workspace_id, where.model_dump(exclude={"limit", "cursor"}))
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        query = _scoped_usage(scope.workspace_id, where)
        page = query.with_only_columns(
            RunRow.created_at, RunRow.id, RunRow.thread_id, maintain_column_froms=True
        ).group_by(RunRow.created_at, RunRow.id, RunRow.thread_id)
        if where.cursor:
            page = page.where(
                tuple_(RunRow.created_at, RunRow.id) > cursors.time_position(where.cursor, "run-usage", owner)
            )
        selected = page.order_by(RunRow.created_at, RunRow.id).limit(where.limit + 1).cte("usage_page")
        numbers = (
            _run_numbers(scope.workspace_id, where)
            .where(RunRow.thread_id.in_(select(selected.c.thread_id)))
            .cte("run_numbers")
        )
        query = (
            query.join(selected, selected.c.id == RunRow.id)
            .join(numbers, numbers.c.id == RunRow.id)
            .add_columns(
                RunRow.created_at,
                RunRow.id.label("run_id"),
                RunRow.thread_id,
                numbers.c.number.label("run_number"),
            )
            .group_by(RunRow.created_at, RunRow.id, RunRow.thread_id, numbers.c.number, ModelRow.key)
            .order_by(RunRow.created_at, RunRow.id, ModelRow.key)
        )
        rows = (await session.execute(query)).mappings().all()
    runs = _run_usage_rows(list(rows))
    items = runs[: where.limit]
    times = {row["run_id"]: row["created_at"] for row in rows}
    return RunUsagePage(
        items=items,
        next_cursor=cursors.encode("run-usage", owner, times[items[-1].run_id].isoformat(), items[-1].run_id)
        if len(runs) > where.limit
        else None,
    )
