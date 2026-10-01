"""Logfire: spans read back with SQL through the project's Query API (v2).

Only values vary in the SQL, each as an escaped literal; columns and attribute names are fixed here. Pages
continue after the last row's (start, trace, span) position, kept at the backend's own timestamp precision.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar, Self

from pydantic import BaseModel, JsonValue, SecretStr, ValidationError

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

_COLUMNS = (
    "trace_id, span_id, parent_span_id, span_name, start_timestamp, end_timestamp,"
    " otel_status_code, otel_status_message, level_name(level) AS level, attributes, otel_resource_attributes,"
    " otel_scope_name, otel_scope_version, otel_events, otel_links"
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
        if query.cursor is not None:
            conditions.append(_after(query.cursor))
        # One extra row tells whether another page follows.
        limit = query.limit + 1
        where = " AND ".join(conditions)
        sql = f"SELECT {_COLUMNS} FROM records WHERE {where} ORDER BY {_ORDER} LIMIT {limit}"
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
