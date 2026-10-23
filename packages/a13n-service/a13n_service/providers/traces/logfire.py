"""Logfire: spans read back with SQL through the project's Query API (v2).

Only values vary in the SQL, each as an escaped literal; columns and attribute names are fixed here. Pages
continue after the last row's (start, trace, span) position, kept at the backend's own timestamp precision.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Self

from pydantic import BaseModel, JsonValue, SecretStr, ValidationError

from a13n_service.infra import cursors
from a13n_service.infra.errors import ServiceError
from a13n_service.providers.traces import (
    Span,
    SpanPage,
    SpanQuery,
    TraceBackendType,
    format_time,
    parse_json_map,
    parse_json_text,
    read_json,
    unavailable,
)
from a13n_service.providers.traces.search import (
    SearchPosition,
    SearchQuery,
    TraceSearchItem,
    TraceSearchPage,
    matches_root,
    summary,
)

_COLUMNS = (
    "trace_id, span_id, parent_span_id, span_name, start_timestamp, end_timestamp,"
    " otel_status_code, otel_status_message, level_name(level) AS level, attributes, otel_resource_attributes,"
    " otel_scope_name, otel_scope_version, otel_events, otel_links"
)
_OVERVIEW_COLUMNS = (
    "trace_id, span_id, parent_span_id, span_name, start_timestamp, end_timestamp,"
    " otel_status_code, level_name(level) AS level, attributes"
)
_ORDER = "start_timestamp DESC, trace_id DESC, span_id DESC"
_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
_SPAN_ID = re.compile(r"^[0-9a-f]{16}$")
_USAGE = "gen_ai.usage."


class _Rows(BaseModel):
    data: list[dict[str, JsonValue]]


@dataclass(frozen=True, slots=True)
class Logfire:
    """One Logfire project: a write token ingests spans, a read token queries them."""

    type: ClassVar[TraceBackendType] = "logfire"
    url: str
    write_token: str = field(repr=False)
    read_token: str = field(repr=False)
    timeout: float  # bounds each query

    @classmethod
    def configure(
        cls,
        url: str | None,
        write_token: SecretStr | None,
        read_token: SecretStr | None,
        *,
        timeout: float,
    ) -> Self:
        if url is None or write_token is None or read_token is None:
            raise ValueError("telemetry: Logfire needs trace_url, logfire_write_token and logfire_read_token")
        return cls(url, write_token.get_secret_value(), read_token.get_secret_value(), timeout)

    @property
    def otlp_endpoint(self) -> str:
        return f"{self.url}/v1/traces"

    @property
    def otlp_headers(self) -> dict[str, str]:
        return {"Authorization": self.write_token}

    @property
    def query_identity(self) -> str:
        return cursors.query_owner(self.type, self.url, self.read_token)

    async def search_traces(self, query: SearchQuery, owner: str) -> TraceSearchPage:
        # SQL performs aggregation, selection, ranking and pagination. No Redis dependency on this path.
        owner = query.page_owner(owner)
        collected_at, position = datetime.now(UTC), None
        if query.cursor:
            values = cursors.decode(query.cursor, "logfire-search-keyset", owner)
            try:
                if len(values) != 4 or not isinstance(values[0], str):
                    raise ValueError("position")
                collected_at = datetime.fromisoformat(values[0])
                if collected_at.tzinfo is None:
                    raise ValueError("time")
                position = SearchPosition.read(values[1:], query)
            except ValueError:
                raise ServiceError("invalid_cursor", "Invalid trace search cursor") from None
        sql = _search_sql(query, collected_at, position)
        response = await read_json(
            self.type,
            "POST",
            f"{self.url}/v2/query",
            timeout=self.timeout,
            headers={"Authorization": self.read_token, "Accept": "application/json"},
            body={
                "sql": sql,
                "min_timestamp": format_time(query.roots.started_after - timedelta(minutes=1)),
                "max_timestamp": format_time(collected_at + timedelta(minutes=1)),
                "limit": query.roots.limit,
            },
        )
        try:
            rows = _Rows.model_validate(response).data
            if not rows:
                raise ValueError("missing count")
            total = int(str(rows[0]["total"]))
            remaining = int(str(rows[0]["remaining"]))
            if not 0 <= remaining <= total or len(rows) > query.roots.limit:
                raise ValueError("count")
            items: list[TraceSearchItem] = []
            for row in rows:
                if int(str(row["total"])) != total or int(str(row["remaining"])) != remaining:
                    raise ValueError("count")
                if row.get("trace_id") is None:
                    if len(rows) != 1 or remaining != 0:
                        raise ValueError("missing rows")
                    continue
                root = _span(row)
                if not matches_root(root, query.roots):
                    raise ValueError("scope")
                item = TraceSearchItem.model_validate(
                    {
                        "root": summary(root),
                        "duration_seconds": row["duration_seconds_text"],
                        "cost_usd": row["trace_cost_text"],
                    }
                )
                if any(value is not None and not value.is_finite() for value in (item.duration_seconds, item.cost_usd)):
                    raise ValueError("metrics")
                items.append(item)
            if len(items) != min(query.roots.limit, remaining):
                raise ValueError("incomplete page")
        except (ValidationError, ValueError, KeyError, TypeError) as error:
            raise unavailable(self.type) from error
        next_cursor = None
        if remaining > len(items):
            last = SearchPosition.from_item(items[-1], query)
            if query.order_by == "started":
                # Keep the backend's timestamp precision; Python datetimes stop at microseconds.
                last = SearchPosition(str(rows[-1]["start_timestamp"]), last.trace_id, last.span_id)
            next_cursor = cursors.encode("logfire-search-keyset", owner, collected_at.isoformat(), *last.values())
        return TraceSearchPage(
            items=items,
            total=total,
            collected_at=collected_at,
            next_cursor=next_cursor,
        )

    async def query(self, query: SpanQuery) -> SpanPage:
        start, end = format_time(query.started_after), format_time(query.started_before)
        conditions = [
            "kind = 'span'",
            f"start_timestamp >= {_literal(start)}",
            f"start_timestamp < {_literal(end)}",
            *(f"attributes->>{_literal(key)} = {_literal(value)}" for key, value in query.attributes.items()),
        ]
        if query.roots:
            conditions.append("parent_span_id IS NULL")
        if query.trace_id is not None:
            conditions.append(f"trace_id = {_literal(query.trace_id)}")
        if query.span_id is not None:
            conditions.append(f"span_id = {_literal(query.span_id)}")
        if query.cursor is not None:
            conditions.append(_after(query.cursor))
        # One extra row tells whether another page follows.
        limit = query.limit + 1
        where = " AND ".join(conditions)
        columns = _COLUMNS if query.include_content else _OVERVIEW_COLUMNS
        sql = f"SELECT {columns} FROM records WHERE {where} ORDER BY {_ORDER} LIMIT {limit}"
        body = {"sql": sql, "min_timestamp": start, "max_timestamp": end, "limit": limit}
        response = await read_json(
            self.type,
            "POST",
            f"{self.url}/v2/query",
            timeout=self.timeout,
            headers={"Authorization": self.read_token, "Accept": "application/json"},
            body=body,
        )
        try:
            rows = _Rows.model_validate(response).data
            items = [_span(row) for row in rows[: query.limit]]
        except ValidationError as error:
            raise unavailable(self.type) from error
        more = len(rows) > query.limit
        return SpanPage(items=items, next_cursor=_position(rows[query.limit - 1]) if more else None)


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _position(row: Mapping[str, JsonValue]) -> str:
    return json.dumps([row.get("start_timestamp"), row.get("trace_id"), row.get("span_id")])


def _after(cursor: str) -> str:
    try:
        started, trace_id, span_id = json.loads(cursor)
        datetime.fromisoformat(started)
        if not (_TRACE_ID.fullmatch(trace_id) and _SPAN_ID.fullmatch(span_id)):
            raise ValueError("position")
    except (ValueError, TypeError):
        raise ServiceError("invalid_cursor", "Invalid collection cursor") from None
    started, trace_id, span_id = _literal(started), _literal(trace_id), _literal(span_id)
    return (
        f"(start_timestamp < {started} OR (start_timestamp = {started} AND trace_id < {trace_id})"
        f" OR (start_timestamp = {started} AND trace_id = {trace_id} AND span_id < {span_id}))"
    )


def _span(row: Mapping[str, JsonValue]) -> Span:
    attributes = parse_json_map(row.get("attributes"))
    usage = {
        key.removeprefix(_USAGE): value
        for key, value in attributes.items()
        if key.startswith(_USAGE) and isinstance(value, int)
    }
    scope = row.get("otel_scope_name")
    return Span.model_validate(
        {
            "trace_id": row.get("trace_id"),
            "id": row.get("span_id"),
            "parent_id": row.get("parent_span_id"),
            "name": row.get("span_name"),
            "kind": attributes.get("langfuse.observation.type") or "span",
            "started_at": row.get("start_timestamp"),
            "ended_at": row.get("end_timestamp"),
            "status": "error" if row.get("otel_status_code") == "ERROR" else "ok",
            "status_message": row.get("otel_status_message"),
            "level": row.get("level"),
            "model": attributes.get("gen_ai.response.model") or attributes.get("gen_ai.request.model"),
            "usage": usage,
            "cost_usd": attributes.get("gen_ai.usage.cost"),
            "input": parse_json_text(attributes.get("a13n.input", attributes.get("gen_ai.input.messages"))),
            "output": parse_json_text(attributes.get("a13n.output", attributes.get("gen_ai.output.messages"))),
            "attributes": attributes,
            "resource_attributes": parse_json_map(row.get("otel_resource_attributes")),
            "scope": {"name": scope, "version": row.get("otel_scope_version")} if scope is not None else None,
            "events": [_event(event) for event in _objects(row.get("otel_events"))],
            "links": [_link(link) for link in _objects(row.get("otel_links"))],
            # Logfire's UI addresses a trace by organization and project names the query API does not return.
            "source_url": None,
        }
    )


def _objects(value: JsonValue) -> list[dict[str, JsonValue]]:
    """The objects of a JSON array column, sent as an array or as JSON text."""
    decoded = parse_json_text(value)
    return [item for item in decoded if isinstance(item, dict)] if isinstance(decoded, list) else []


def _event(event: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    attributes = parse_json_map(event.get("attributes"))
    return {"name": event.get("event_name"), "timestamp": event.get("event_timestamp"), "attributes": attributes}


def _link(link: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    context, attributes = parse_json_map(link.get("context")), parse_json_map(link.get("attributes"))
    return {"trace_id": context.get("trace_id"), "span_id": context.get("span_id"), "attributes": attributes}


def _search_sql(query: SearchQuery, collected_at: datetime, position: SearchPosition | None) -> str:
    root = query.roots
    scope = " AND ".join(
        f"attributes->>{_literal(key)} = {_literal(value)}" for key, value in query.correlation.items()
    )
    selectors = " AND ".join(
        f"attributes->>{_literal(key)} = {_literal(value)}" for key, value in root.attributes.items()
    )
    predicates = []
    for column, minimum, maximum in (
        ("duration_seconds", query.min_duration_seconds, query.max_duration_seconds),
        ("trace_cost", query.min_cost_usd, query.max_cost_usd),
    ):
        if minimum is not None:
            predicates.append(f"{column} >= {format(minimum, 'f')}")
        if maximum is not None:
            predicates.append(f"{column} <= {format(maximum, 'f')}")
    where = " AND ".join(predicates) or "TRUE"
    column = {"started": "start_timestamp", "duration": "duration_seconds", "cost": "trace_cost"}[query.order_by]
    direction = "ASC" if query.direction == "asc" else "DESC"
    order = f"{column} {direction} NULLS LAST, trace_id ASC, span_id ASC"
    after = "TRUE"
    if position is not None:
        identity_after = (
            f"(trace_id > {_literal(position.trace_id)} OR "
            f"(trace_id = {_literal(position.trace_id)} AND span_id > {_literal(position.span_id)}))"
        )
        if position.value is None:
            after = f"({column} IS NULL AND {identity_after})"
        else:
            value = _literal(position.value) if query.order_by == "started" else position.value
            operator = ">" if query.direction == "asc" else "<"
            after = f"({column} IS NULL OR {column} {operator} {value} OR ({column} = {value} AND {identity_after}))"
    # Select roots in the narrow window before deduplicating their spans. The outer Query API window
    # still covers later children, but unrelated traces must not enter that wider deduplication.
    # Root metadata applies after root deduplication and never filters child costs.
    return f"""
WITH root_versions AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY trace_id, span_id ORDER BY end_timestamp DESC NULLS LAST, start_timestamp DESC
    ) AS occurrence
    FROM records
    WHERE kind = 'span' AND parent_span_id IS NULL
      AND start_timestamp >= {_literal(format_time(root.started_after))}
      AND start_timestamp < {_literal(format_time(root.started_before))}
      AND {scope}
), root_candidates AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY trace_id ORDER BY start_timestamp DESC, span_id ASC) AS root_number
    FROM root_versions WHERE occurrence = 1 AND {selectors}
), roots AS (
    SELECT * FROM root_candidates WHERE root_number = 1
), scoped AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY trace_id, span_id ORDER BY end_timestamp DESC NULLS LAST, start_timestamp DESC
    ) AS occurrence
    FROM records
    WHERE kind = 'span'
      AND start_timestamp >= {_literal(format_time(root.started_after - timedelta(minutes=1)))}
      AND start_timestamp < {_literal(format_time(collected_at + timedelta(minutes=1)))}
      AND {scope}
      AND trace_id IN (SELECT trace_id FROM roots)
), spans AS (
    SELECT * FROM scoped WHERE occurrence = 1
), costs AS (
    SELECT s.trace_id, SUM(TRY_CAST(s.attributes->>'gen_ai.usage.cost' AS DECIMAL(38, 15))) AS trace_cost
    FROM spans s GROUP BY s.trace_id
), metrics AS (
    SELECT r.trace_id, r.span_id, r.parent_span_id, r.span_name, r.start_timestamp, r.end_timestamp,
           r.otel_status_code, r.otel_status_message, level_name(r.level) AS level, r.attributes,
           CAST(r.duration AS DECIMAL(38, 9)) AS duration_seconds, c.trace_cost
    FROM roots r LEFT JOIN costs c ON r.trace_id = c.trace_id
), filtered AS (
    SELECT * FROM metrics WHERE {where}
), eligible AS (
    SELECT * FROM filtered WHERE {after}
), totals AS (
    SELECT (SELECT COUNT(*) FROM filtered) AS total, COUNT(*) AS remaining FROM eligible
), page AS (
    SELECT * FROM eligible ORDER BY {order} LIMIT {root.limit}
)
SELECT page.trace_id, page.span_id, page.parent_span_id, page.span_name, page.start_timestamp, page.end_timestamp,
       page.otel_status_code, page.otel_status_message, page.level, page.attributes,
       CAST(page.duration_seconds AS VARCHAR) AS duration_seconds_text, CAST(page.trace_cost AS VARCHAR) AS trace_cost_text,
       totals.total, totals.remaining
FROM totals LEFT JOIN page ON TRUE
ORDER BY page.{column} {direction} NULLS LAST, page.trace_id ASC, page.span_id ASC
"""
