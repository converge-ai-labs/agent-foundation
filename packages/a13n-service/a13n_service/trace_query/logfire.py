"""Logfire public Query API adapter over completed span records."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast
from urllib.parse import urlsplit

import httpx2
from pydantic import JsonValue, ValidationError

from . import decoding
from .domain import (
    Content,
    InstrumentationScope,
    ModelIdentity,
    Observation,
    ObservationCollection,
    ObservationEvent,
    ObservationLink,
    ProviderTraceQuery,
    ProviderTraceRead,
    SearchIn,
    Trace,
    TraceCollection,
    TraceCorrelation,
    TraceQueryCapabilities,
    TraceStatus,
    TraceView,
    project_observation,
)
from .errors import TraceQueryProviderError

_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_ROOT_NAME = "a13n.service.run_attempt"
_CORRELATION = {
    "organization_id": "a13n.organization.id",
    "workspace_id": "a13n.workspace.id",
    "session_id": "a13n.observation.session.id",
    "thread_id": "a13n.thread.id",
    "run_id": "a13n.service.run.id",
    "run_attempt_id": "a13n.run_attempt.id",
    "agent_id": "a13n.agent.preset.id",
}
_COLUMNS = (
    "trace_id, span_id, parent_span_id, span_name, start_timestamp, end_timestamp, "
    "otel_status_code, level_name(level) AS level, attributes"
)
_COMPACT_ATTRIBUTE_KEYS = (
    *_CORRELATION.values(),
    "openinference.span.kind",
    "langfuse.observation.type",
    "gen_ai.operation.name",
    "gen_ai.request.model",
    "gen_ai.response.model",
)
_FULL_COLUMNS = (
    ", otel_status_message, otel_resource_attributes, otel_scope_name, otel_scope_version, "
    "otel_scope_attributes, otel_events, otel_links"
)


class LogfireTraceQueryProvider:
    """One bounded SQL page with a stable timestamp/identity keyset."""

    def __init__(self, client: httpx2.AsyncClient, *, base_url: str, read_token: str, history_from: datetime) -> None:
        self._client = client
        self._base_url = validate_logfire_base_url(base_url)
        if not read_token or history_from.tzinfo is None:
            raise ValueError("Logfire requires a read token and timezone-aware history_from")
        self._read_token = read_token
        self._history_from = history_from
        self._namespace = hashlib.sha256(
            json.dumps([self._base_url, read_token, decoding.format_datetime(history_from)]).encode()
        ).hexdigest()

    @property
    def cursor_namespace(self) -> str:
        return self._namespace

    @property
    def capabilities(self) -> TraceQueryCapabilities:
        return TraceQueryCapabilities(search_in=tuple(SearchIn), history_from=self._history_from)

    async def list_traces(self, query: ProviderTraceQuery) -> TraceCollection:
        filters = _root_filters(query.organization_id, query.workspace_id)
        for name in ("thread_id", "run_id", "run_attempt_id"):
            value = {"thread_id": query.thread_id, "run_id": query.run_id, "run_attempt_id": query.run_attempt_id}[name]
            if value is not None:
                filters.append(_attribute_equals(_CORRELATION[name], value))
        if query.query is not None:
            if query.search_in not in self.capabilities.search_in:
                raise TraceQueryProviderError("filter_unsupported")
            targets = ("input", "output") if query.search_in is SearchIn.input_output else (str(query.search_in),)
            filters.append(
                "("
                + " OR ".join(
                    f"strpos(lower(coalesce(attributes->>'{target}.value', attributes->>'a13n.{target}', "
                    f"attributes->>'gen_ai.{target}.messages')), lower({_literal(query.query)})) > 0"
                    for target in targets
                )
                + ")"
            )
        rows, cursor = await self._page(
            filters, query.from_started_at, query.to_started_at, query.view, query.limit, query.cursor
        )
        return TraceCollection(items=tuple(_trace(row, query.view) for row in rows), next_cursor=cursor)

    async def get_trace(self, query: ProviderTraceRead) -> Trace | None:
        filters = _root_filters(query.organization_id, query.workspace_id)
        filters.append(f"trace_id = {_literal(query.trace_id)}")
        rows, cursor = await self._page(filters, self._history_from, query.to_started_at, query.view, 2, None)
        if len(rows) > 1 or cursor is not None:
            raise TraceQueryProviderError("malformed")
        if not rows:
            return None
        trace = _trace(rows[0], query.view)
        if trace.id != query.trace_id:
            raise TraceQueryProviderError("malformed")
        if (trace.correlation.organization_id, trace.correlation.workspace_id) != (
            query.organization_id,
            query.workspace_id,
        ):
            return None
        return trace

    async def list_observations(self, query: ProviderTraceRead) -> ObservationCollection:
        rows, cursor = await self._page(
            [f"trace_id = {_literal(query.trace_id)}"],
            self._history_from,
            query.to_started_at,
            query.view,
            query.limit,
            query.cursor,
        )
        if any(row.get("trace_id") != query.trace_id for row in rows):
            raise TraceQueryProviderError("malformed")
        return ObservationCollection(items=tuple(_observation(row, query.view) for row in rows), next_cursor=cursor)

    async def _page(
        self,
        filters: list[str],
        start: datetime,
        end: datetime,
        view: TraceView,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[Mapping[str, Any]], str | None]:
        if start < self._history_from:
            raise TraceQueryProviderError("filter_unsupported")
        filters = [
            "kind = 'span'",
            *filters,
            f"start_timestamp >= {_literal(decoding.format_datetime(start))}",
            f"start_timestamp < {_literal(decoding.format_datetime(end))}",
        ]
        if cursor is not None:
            filters.append(_continuation_filter(cursor))
        sql = (
            f"SELECT {_COLUMNS}{_FULL_COLUMNS if view is TraceView.full else ''} FROM records WHERE "
            + " AND ".join(filters)
            + f" ORDER BY start_timestamp DESC, trace_id DESC, span_id DESC LIMIT {limit + 1}"
        )
        if view is TraceView.compact:
            sql = _compact_sql(sql)
        rows = await self._query(sql, start, end, limit + 1)
        if len(rows) > limit + 1:
            raise TraceQueryProviderError("response_too_large")
        next_cursor = _cursor(rows[limit - 1]) if len(rows) > limit else None
        return rows[:limit], next_cursor

    async def _query(self, sql: str, start: datetime, end: datetime, limit: int) -> list[Mapping[str, Any]]:
        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/v2/query",
                headers={"Authorization": f"Bearer {self._read_token}", "Accept": "application/json"},
                json={
                    "sql": sql,
                    "min_timestamp": decoding.format_datetime(start),
                    "max_timestamp": decoding.format_datetime(end),
                    "limit": limit,
                },
            ) as response:
                if response.status_code == 404:
                    raise TraceQueryProviderError("version_unsupported")
                if response.status_code != 200:
                    raise TraceQueryProviderError("unavailable")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_RESPONSE_BYTES:
                        raise TraceQueryProviderError("response_too_large")
        except httpx2.HTTPError as error:
            raise TraceQueryProviderError("unavailable") from error
        try:
            payload = json.loads(body)
        except (ValueError, RecursionError) as error:
            raise TraceQueryProviderError("malformed") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise TraceQueryProviderError("malformed")
        rows = payload["data"]
        if any(not isinstance(row, dict) for row in rows):
            raise TraceQueryProviderError("malformed")
        return rows


def _compact_sql(page_sql: str) -> str:
    # Expand only the bounded page, retaining arbitrary reported usage keys and
    # raw JSON values. Never transport I/O or arbitrary attribute bodies just to
    # discard them in Python. A left join keeps spans with no matching keys.
    keys = ", ".join(_literal(key) for key in _COMPACT_ATTRIBUTE_KEYS)
    return (
        f"WITH page AS ({page_sql}), "
        "attribute_keys AS (SELECT trace_id, span_id, attributes, "
        "UNNEST(json_object_keys(attributes)) AS attribute_key FROM page), "
        "compact AS (SELECT trace_id, span_id, "
        "'{' || string_agg(json_union_to_text(json_from_scalar(attribute_key)) || ':' || "
        "json_get_json(attributes, attribute_key), ',') || '}' AS attributes "
        f"FROM attribute_keys WHERE attribute_key IN ({keys}) "
        "OR starts_with(attribute_key, 'gen_ai.usage.') GROUP BY trace_id, span_id) "
        "SELECT page.trace_id, page.span_id, page.parent_span_id, page.span_name, "
        "page.start_timestamp, page.end_timestamp, page.otel_status_code, page.level, "
        "coalesce(compact.attributes, '{}') AS attributes FROM page LEFT JOIN compact "
        "ON page.trace_id = compact.trace_id AND page.span_id = compact.span_id "
        "ORDER BY page.start_timestamp DESC, page.trace_id DESC, page.span_id DESC"
    )


def _root_filters(organization_id: str, workspace_id: str) -> list[str]:
    return [
        f"span_name = '{_ROOT_NAME}'",
        "parent_span_id IS NULL",
        _attribute_equals(_CORRELATION["organization_id"], organization_id),
        _attribute_equals(_CORRELATION["workspace_id"], workspace_id),
    ]


def _attribute_equals(key: str, value: str) -> str:
    return f"attributes->>{_literal(key)} = {_literal(value)}"


def _literal(value: str) -> str:
    # Only values are variable. Column names and operators are adapter-owned.
    if "\x00" in value:
        raise TraceQueryProviderError("malformed")
    return "'" + value.replace("'", "''") + "'"


def _cursor(row: Mapping[str, Any]) -> str:
    # Retain the backend's full timestamp precision, not datetime's microseconds.
    return json.dumps(
        [
            decoding.required_text(row.get(name), name, max_bytes=512)
            for name in ("start_timestamp", "trace_id", "span_id")
        ],
        separators=(",", ":"),
    )


def _continuation_filter(cursor: str) -> str:
    try:
        values = json.loads(cursor)
        if not isinstance(values, list) or len(values) != 3 or any(not isinstance(x, str) or not x for x in values):
            raise ValueError
        time, trace, span = values
        decoding.datetime(time, "start_timestamp")
    except (ValueError, TraceQueryProviderError) as error:
        raise TraceQueryProviderError("invalid_cursor") from error
    t, tr, sp = (_literal(value) for value in (time, trace, span))
    return (
        f"(start_timestamp < {t} OR (start_timestamp = {t} AND trace_id < {tr}) "
        f"OR (start_timestamp = {t} AND trace_id = {tr} AND span_id < {sp}))"
    )


def _json(value: object) -> JsonValue:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError) as error:
            raise TraceQueryProviderError("malformed") from error
    decoding.bounded_json(value, max_bytes=256 * 1024)
    return cast(JsonValue, value)


def _attributes(value: object) -> Mapping[str, JsonValue] | None:
    result = _json(value)
    if result is not None and not isinstance(result, dict):
        raise TraceQueryProviderError("malformed")
    return result


def _trace(row: Mapping[str, Any], view: TraceView) -> Trace:
    if row.get("span_name") != _ROOT_NAME or row.get("parent_span_id") is not None:
        raise TraceQueryProviderError("malformed")
    attributes = _attributes(row.get("attributes")) or {}
    return Trace(
        id=decoding.required_text(row.get("trace_id"), "trace_id", max_bytes=512),
        provider="logfire",
        correlation=TraceCorrelation(
            **{name: decoding.required_text(attributes.get(key), key) for name, key in _CORRELATION.items()}
        ),
        root=_observation(row, view),
        source_url=None,
    )


def _status(value: object) -> TraceStatus | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1, 2):
        return ("unset", "ok", "error")[value]
    if isinstance(value, str) and value.lower() in ("unset", "ok", "error"):
        return cast(TraceStatus, value.lower())
    raise TraceQueryProviderError("malformed")


def _events(value: object) -> tuple[ObservationEvent, ...] | None:
    values = _json(value)
    if values is None:
        return None
    if not isinstance(values, list):
        raise TraceQueryProviderError("malformed")
    events = []
    for item in values:
        if not isinstance(item, dict):
            raise TraceQueryProviderError("malformed")
        events.append(
            ObservationEvent(
                name=decoding.required_text(item.get("event_name"), "event name"),
                occurred_at=decoding.datetime(item.get("event_timestamp"), "event timestamp"),
                attributes=_attributes(item.get("attributes")) or {},
            )
        )
    return tuple(events)


def _links(value: object) -> tuple[ObservationLink, ...] | None:
    values = _json(value)
    if values is None:
        return None
    if not isinstance(values, list):
        raise TraceQueryProviderError("malformed")
    links = []
    for item in values:
        if not isinstance(item, dict):
            raise TraceQueryProviderError("malformed")
        context = item.get("context")
        if not isinstance(context, dict):
            raise TraceQueryProviderError("malformed")
        links.append(
            ObservationLink(
                trace_id=decoding.required_text(context.get("trace_id"), "link trace_id"),
                observation_id=decoding.required_text(context.get("span_id"), "link span_id"),
                attributes=_attributes(item.get("attributes")),
            )
        )
    return tuple(links)


def _observation(row: Mapping[str, Any], view: TraceView) -> Observation:
    attributes = _attributes(row.get("attributes"))
    attrs = attributes or {}
    requested = decoding.optional_text(attrs.get("gen_ai.request.model"), "requested model")
    response = decoding.optional_text(attrs.get("gen_ai.response.model"), "response model")
    usage = {
        key: value for key, value in attrs.items() if key.startswith("gen_ai.usage.") and key != "gen_ai.usage.cost"
    }
    try:
        item = Observation(
            id=decoding.required_text(row.get("span_id"), "span_id", max_bytes=512),
            parent_id=decoding.optional_text(row.get("parent_span_id"), "parent_span_id", max_bytes=512),
            type=_operation_type(attrs),
            name=decoding.required_text(row.get("span_name"), "span_name"),
            started_at=decoding.datetime(row.get("start_timestamp"), "start_timestamp"),
            ended_at=decoding.optional_datetime(row.get("end_timestamp"), "end_timestamp"),
            status=_status(row.get("otel_status_code")),
            level=decoding.optional_text(row.get("level"), "level"),
            status_message=row.get("otel_status_message"),
            model=ModelIdentity(requested=requested, response=response)
            if requested is not None or response is not None
            else None,
            usage=decoding.usage(usage) if usage else None,
            cost_usd=decoding.decimal(attrs.get("gen_ai.usage.cost")),
            input=_content(attrs, "input") if view is TraceView.full else None,
            output=_content(attrs, "output") if view is TraceView.full else None,
            attributes=attributes,
            resource_attributes=_attributes(row.get("otel_resource_attributes")),
            scope=InstrumentationScope(
                name=row.get("otel_scope_name"),
                version=row.get("otel_scope_version"),
                attributes=_attributes(row.get("otel_scope_attributes")),
            )
            if view is TraceView.full
            else None,
            events=_events(row.get("otel_events")),
            links=_links(row.get("otel_links")),
        )
    except ValidationError as error:
        raise TraceQueryProviderError("malformed") from error
    return project_observation(item, view)


def _operation_type(attributes: Mapping[str, JsonValue]) -> str:
    for key in ("openinference.span.kind", "langfuse.observation.type"):
        if attributes.get(key) is not None:
            return decoding.required_text(attributes[key], "type").lower()
    operation = attributes.get("gen_ai.operation.name")
    if operation is None:
        return "span"
    name = decoding.required_text(operation, "operation")
    return {
        "invoke_agent": "agent",
        "execute_tool": "tool",
        "chat": "generation",
        "text_completion": "generation",
        "generate_content": "generation",
    }.get(name, name)


def _content(attributes: Mapping[str, JsonValue], direction: str) -> Content | None:
    # Logfire's records attributes already contain decoded JSON, including
    # strings and null. Decoding these values again corrupts JSON-looking text
    # and rejects ordinary Harness output. Key presence distinguishes reported
    # null from missing content; _attributes has already bounded the values.
    for key, media_type in (
        (f"{direction}.value", attributes.get(f"{direction}.mime_type")),
        (f"a13n.{direction}", "application/json"),
        (f"gen_ai.{direction}.messages", "application/json"),
    ):
        if key in attributes:
            return Content(
                media_type=decoding.optional_text(media_type, "media_type", max_bytes=256), value=attributes[key]
            )
    return None


def validate_logfire_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Logfire base URL must be an origin without credentials")
    return value.rstrip("/")
