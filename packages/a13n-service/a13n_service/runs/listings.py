"""Run discovery: narrow PostgreSQL reads with bounded, bidirectional history pages."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, func, literal, select, true, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, load_only

from a13n_service.infra import cursors, reads
from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import ServiceError, invalid, not_found
from a13n_service.runs.previews import input_text
from a13n_service.runs.schemas import RunInputPreview, RunListItem, RunPage, RunQuery, ThreadRunPage
from a13n_service.runs.search import predicate
from a13n_service.runs.tables import InboxEntryRow, RunRow, ThreadRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal

FIELDS = tuple(name for name in RunListItem.model_fields if name != "input_preview")


def query() -> Select[tuple[RunRow]]:
    """Fail closed if a list accidentally accesses full payloads, settings or checkpoints."""
    return select(RunRow).options(load_only(*(getattr(RunRow, name) for name in FIELDS), raiseload=True))


async def views(session: AsyncSession, rows: Sequence[RunRow]) -> list[RunListItem]:
    if not rows:
        return []
    previews = dict(
        (
            await session.execute(
                select(RunRow.id, input_text(201))
                .outerjoin(InboxEntryRow, InboxEntryRow.id == RunRow.source_entry_id)
                .where(RunRow.id.in_([row.id for row in rows]))
            )
        )
        .tuples()
        .all()
    )
    return [
        RunListItem(
            **{name: getattr(row, name) for name in FIELDS},
            input_preview=RunInputPreview(
                text=(previews[row.id] or "")[:200], truncated=len(previews[row.id] or "") > 200
            ),
        )
        for row in rows
    ]


async def list_runs(storage: Storage, actor: Principal, workspace_id: str, filters: RunQuery) -> RunPage:
    match = predicate(RunRow.search_vector, filters.text_query)
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        statement = query().where(RunRow.workspace_id == scope.workspace_id)
        for name in ("agent_id", "session_id", "thread_id", "principal_id", "status"):
            if (value := getattr(filters, name)) is not None:
                statement = statement.where(getattr(RunRow, name) == value)
        if filters.created_after is not None:
            statement = statement.where(RunRow.created_at >= filters.created_after)
        if filters.created_before is not None:
            statement = statement.where(RunRow.created_at < filters.created_before)
        if match is not None:
            statement = statement.where(match)
        rows, following = await cursors.keyset_page(
            session,
            statement,
            (RunRow.created_at, RunRow.id),
            kind="runs",
            owner=cursors.query_owner(scope.workspace_id, filters.model_dump(mode="json", exclude={"limit", "cursor"})),
            cursor=filters.cursor,
            limit=filters.limit,
            newest_first=True,
        )
        values = await views(session, rows)
        count = reads.fit_count(values)
        if count < len(rows):
            last = rows[count - 1]
            following = cursors.encode(
                "runs",
                cursors.query_owner(scope.workspace_id, filters.model_dump(mode="json", exclude={"limit", "cursor"})),
                last.created_at.isoformat(),
                last.id,
            )
        return RunPage(items=values[:count], next_cursor=following)


async def list_thread_runs(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    thread_id: str,
    *,
    limit: int,
    cursor: str | None,
    anchor: str | None = None,
) -> ThreadRunPage:
    """Start at the newest run or an anchor; page in either direction without loading intervening runs."""
    if cursor is not None and anchor is not None:
        raise invalid("anchor", "anchor and cursor exclude each other")
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        if (
            await session.scalar(
                select(ThreadRow.id).where(ThreadRow.workspace_id == scope.workspace_id, ThreadRow.id == thread_id)
            )
            is None
        ):
            raise not_found("thread", thread_id)
        scoped = (RunRow.workspace_id == scope.workspace_id, RunRow.thread_id == thread_id)
        owner = cursors.query_owner(scope.workspace_id, thread_id)
        statement = query().where(*scoped)
        order = tuple_(RunRow.created_at, RunRow.id)
        newer = False
        if cursor is not None:
            position = cursors.decode(cursor, "thread_runs", owner)
            try:
                direction, timestamp, key = position
                if (
                    direction not in ("older", "newer")
                    or not isinstance(timestamp, str)
                    or not isinstance(key, str)
                    or len(key) > 72
                ):
                    raise ValueError("position")
                time = datetime.fromisoformat(timestamp)
                if time.tzinfo is None:
                    raise ValueError("timestamp")
            except (TypeError, ValueError):
                raise ServiceError("invalid_cursor", "Invalid collection cursor") from None
            newer = direction == "newer"
            boundary = tuple_(literal(time), literal(key))
            statement = statement.where(order > boundary if newer else order < boundary)
        elif anchor is not None:
            target = (
                await session.execute(select(RunRow.created_at, RunRow.id).where(*scoped, RunRow.id == anchor))
            ).one_or_none()
            if target is None:
                raise not_found("run", anchor)
            statement = statement.where(order <= tuple_(*map(literal, target)))
        ordered = (RunRow.created_at, RunRow.id) if newer else (RunRow.created_at.desc(), RunRow.id.desc())
        # Select the bounded metadata window and its counts in one PostgreSQL snapshot.
        # The aggregate scans this thread's index once; it never hydrates other Run payloads.
        page = (
            statement.with_only_columns(*(getattr(RunRow, name) for name in FIELDS))
            .order_by(*ordered)
            .limit(limit)
            .cte("run_page")
        )
        page_order = (page.c.created_at, page.c.id)
        if not newer:
            page_order = tuple(column.desc() for column in page_order)
        first = select(page.c.created_at, page.c.id).order_by(*page_order).limit(1).scalar_subquery()
        counts = (
            select(func.count().label("total"), func.count().filter(order <= first).label("number"))
            .select_from(RunRow)
            .where(*scoped)
            .cte("run_counts")
        )
        run = aliased(RunRow, page)
        result = (
            await session.execute(
                select(run, counts.c.total, counts.c.number)
                .select_from(counts.outerjoin(page, true()))
                .options(load_only(*(getattr(run, name) for name in FIELDS), raiseload=True))
                .order_by(*page_order)
            )
        ).all()
        total, number = result[0][1:]
        rows = [row[0] for row in result if row[0] is not None]
        values = await views(session, rows)
        count = reads.fit_count(values)
        rows, values = rows[:count], values[:count]
        if newer:
            rows.reverse()
            values.reverse()
            number += len(rows) - 1 if rows else 0

        def cursor_at(direction: str, row: RunRow) -> str:
            return cursors.encode("thread_runs", owner, direction, row.created_at.isoformat(), row.id)

        return ThreadRunPage(
            items=values,
            total=total,
            first_number=number,
            previous_cursor=cursor_at("newer", rows[0]) if rows and number < total else None,
            next_cursor=cursor_at("older", rows[-1]) if rows and number > len(rows) else None,
        )


async def lineage(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    run_id: str,
    *,
    cursor: str | None,
    limit: int = 50,
) -> RunPage:
    """A bounded recursive read of the run and its ancestors, including fork origins."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        start = cursors.id_position(cursor, "lineage", run_id) or run_id
        chain = (
            select(RunRow.id, RunRow.parent_run_id, literal(1).label("depth"))
            .where(
                RunRow.workspace_id == scope.workspace_id,
                RunRow.id == start,
            )
            .cte("lineage", recursive=True)
        )
        chain = chain.union_all(
            select(RunRow.id, RunRow.parent_run_id, chain.c.depth + 1)
            .join(
                chain,
                RunRow.id == chain.c.parent_run_id,
            )
            .where(RunRow.workspace_id == scope.workspace_id, chain.c.depth < limit)
        )
        rows = (await session.scalars(query().join(chain, RunRow.id == chain.c.id).order_by(chain.c.depth))).all()
        if not rows:
            raise not_found("run", start)
        values = await views(session, rows)
        count = reads.fit_count(values)
        older = rows[count - 1].parent_run_id
        return RunPage(items=values[:count], next_cursor=cursors.encode("lineage", run_id, older) if older else None)
