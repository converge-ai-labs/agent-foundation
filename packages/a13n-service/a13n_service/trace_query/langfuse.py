"""Langfuse v4 Observations API v2 read adapter."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import quote, urlsplit, urlunsplit

import httpx2
from pydantic import JsonValue, ValidationError

from .decoding import (
    bounded_json as _bounded_json,
)
from .decoding import (
    datetime as _datetime,
)
from .decoding import (
    decimal as _decimal,
)
from .decoding import (
    format_datetime as _format_datetime,
)
from .decoding import (
    io_value as _io_value,
)
from .decoding import (
    optional_datetime as _optional_datetime,
)
from .decoding import (
    optional_text as _optional_text,
)
from .decoding import (
    required_text as _required_text,
)
from .decoding import (
    usage as _usage,
)
from .domain import (
    InstrumentationScope,
    ModelIdentity,
    Observation,
    ObservationCollection,
    ProviderTraceQuery,
    ProviderTraceRead,
    SearchIn,
    Trace,
    TraceCollection,
    TraceCorrelation,
    TraceQueryCapabilities,
    TraceView,
    project_observation,
)
from .errors import TraceQueryProviderError

_OBSERVATIONS_PATH = "/api/public/v2/observations"
_ROOT_NAME = "a13n.service.run_attempt"
_FIELD_GROUPS = "core,basic,io,metadata,model,usage,metrics,trace_context"
_COMPACT_FIELD_GROUPS = "core,basic,metadata,model,usage,metrics,trace_context"
_EXPANDED_METADATA_KEYS = "attributes,resourceAttributes,scope"
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_METADATA_BYTES = 256 * 1024
_MAX_METADATA_ENTRIES = 512


class LangfuseTraceQueryProvider:
    """Read one bounded native page; never walk a whole trace internally."""

    def __init__(self, client: httpx2.AsyncClient, *, base_url: str, public_key: str, secret_key: str) -> None:
        self._client = client
        self._base_url = _validate_base_url(base_url)
        if not public_key or not secret_key:
            raise ValueError("Langfuse query credentials are required")
        self._auth = httpx2.BasicAuth(public_key, secret_key)
        self._namespace = hashlib.sha256(json.dumps([self._base_url, public_key, secret_key]).encode()).hexdigest()

    @property
    def cursor_namespace(self) -> str:
        return self._namespace

    @property
    def capabilities(self) -> TraceQueryCapabilities:
        return TraceQueryCapabilities(search_in=(SearchIn.input, SearchIn.output))

    async def list_traces(self, query: ProviderTraceQuery) -> TraceCollection:
        filters = _base_filters() + _scope_filters(query.organization_id, query.workspace_id)
        filters.extend(
            (
                _filter("datetime", "startTime", ">=", _format_datetime(query.from_started_at)),
                _filter("datetime", "startTime", "<", _format_datetime(query.to_started_at)),
            )
        )
        if query.thread_id is not None:
            filters.append(_filter("string", "sessionId", "=", query.thread_id))
        if query.run_id is not None:
            filters.append(_metadata_filter("a13n.service.run.id", query.run_id))
        if query.run_attempt_id is not None:
            filters.append(_metadata_filter("a13n.run_attempt.id", query.run_attempt_id))
        if query.query is not None:
            if query.search_in not in self.capabilities.search_in:
                raise TraceQueryProviderError("filter_unsupported")
            filters.append(_filter("string", str(query.search_in), "matches", query.query))
        rows, cursor = await self._page(query.view, query.limit, filters, cursor=query.cursor)
        traces = tuple(self._trace(row, query.view) for row in rows)
        return TraceCollection(
            items=tuple(trace for trace in traces if _matches_query_correlation(trace, query)), next_cursor=cursor
        )

    async def get_trace(self, query: ProviderTraceRead) -> Trace | None:
        filters = _base_filters() + _scope_filters(query.organization_id, query.workspace_id) + _history_filters(query)
        rows, cursor = await self._page(query.view, 2, filters, trace_id=query.trace_id)
        if cursor is not None or len(rows) > 1:
            raise TraceQueryProviderError("malformed")
        if not rows:
            return None
        trace = self._trace(rows[0], query.view)
        if trace.id != query.trace_id:
            raise TraceQueryProviderError("malformed")
        if (
            trace.correlation.organization_id != query.organization_id
            or trace.correlation.workspace_id != query.workspace_id
        ):
            return None
        return trace

    async def list_observations(self, query: ProviderTraceRead) -> ObservationCollection:
        rows, cursor = await self._page(
            query.view, query.limit, _history_filters(query), trace_id=query.trace_id, cursor=query.cursor
        )
        # Children need not repeat Service root attributes. The trace identity
        # associates them with the independently authorized scoped root.
        if any(row.get("traceId") != query.trace_id for row in rows):
            raise TraceQueryProviderError("malformed")
        return ObservationCollection(
            items=tuple(_observation(row, view=query.view) for row in rows), next_cursor=cursor
        )

    async def _page(
        self,
        view: TraceView,
        limit: int,
        filters: list[dict[str, Any]],
        *,
        trace_id: str | None = None,
        cursor: str | None = None,
    ) -> tuple[list[Mapping[str, Any]], str | None]:
        payload = await self._get_observations(
            {
                "fields": _FIELD_GROUPS if view is TraceView.full else _COMPACT_FIELD_GROUPS,
                "expandMetadata": _EXPANDED_METADATA_KEYS,
                "limit": str(limit),
                "filter": json.dumps(filters, separators=(",", ":")),
                **({"traceId": trace_id} if trace_id is not None else {}),
                **({"cursor": cursor} if cursor is not None else {}),
            }
        )
        rows, next_cursor = _page(payload)
        if len(rows) > limit:
            raise TraceQueryProviderError("response_too_large")
        if next_cursor is not None and next_cursor == cursor:
            raise TraceQueryProviderError("malformed")
        return rows, next_cursor

    async def _get_observations(self, params: Mapping[str, str]) -> Mapping[str, Any]:
        try:
            async with self._client.stream(
                "GET", f"{self._base_url}{_OBSERVATIONS_PATH}", params=params, auth=self._auth
            ) as response:
                if response.status_code == 404:
                    raise TraceQueryProviderError("version_unsupported")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_RESPONSE_BYTES:
                        raise TraceQueryProviderError("response_too_large")
        except httpx2.HTTPError as error:
            raise TraceQueryProviderError("unavailable") from error
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise TraceQueryProviderError("malformed" if response.status_code == 200 else "unavailable") from error
        if response.status_code != 200:
            if response.status_code == 400 and isinstance(value, Mapping):
                message = value.get("message")
                if isinstance(message, str) and message.lower().startswith(("invalid cursor", "cursor expired")):
                    raise TraceQueryProviderError("invalid_cursor")
            raise TraceQueryProviderError("unavailable")
        if not isinstance(value, Mapping):
            raise TraceQueryProviderError("malformed")
        return cast(Mapping[str, Any], value)

    def _trace(self, item: Mapping[str, Any], view: TraceView) -> Trace:
        if not _is_run_attempt_root(item):
            raise TraceQueryProviderError("malformed")
        trace_id = _required_text(item.get("traceId"), "traceId", max_bytes=512)
        attributes = _otel_attributes(_metadata(item.get("metadata")))
        correlation = TraceCorrelation(
            organization_id=_attribute(attributes, "a13n.organization.id"),
            workspace_id=_attribute(attributes, "a13n.workspace.id"),
            session_id=_attribute(attributes, "a13n.observation.session.id"),
            thread_id=_attribute(attributes, "a13n.thread.id"),
            run_id=_attribute(attributes, "a13n.service.run.id"),
            run_attempt_id=_attribute(attributes, "a13n.run_attempt.id"),
            agent_id=_attribute(attributes, "a13n.agent.preset.id"),
        )
        project_id = _required_text(item.get("projectId"), "projectId", max_bytes=512)
        return Trace(
            id=trace_id,
            provider="langfuse",
            correlation=correlation,
            root=_observation(item, view=view),
            source_url=f"{self._base_url}/project/{quote(project_id, safe='')}/traces/{quote(trace_id, safe='')}",
        )


def _observation(item: Mapping[str, Any], *, view: TraceView) -> Observation:
    metadata = _metadata(item.get("metadata"))
    attributes = dict(_otel_attributes(metadata))
    requested = _optional_text(attributes.get("gen_ai.request.model"), "gen_ai.request.model")
    response = _optional_text(attributes.get("gen_ai.response.model"), "gen_ai.response.model")
    # A vendor display label does not establish request/response identity.
    label = item.get("model") or item.get("providedModelName")
    if label is not None:
        attributes["langfuse.model"] = _required_text(label, "model")
    scope = _metadata_namespace(metadata, "scope")
    level = _optional_text(item.get("level"), "level")
    try:
        observation = Observation(
            id=_required_text(item.get("id"), "id", max_bytes=512),
            parent_id=_optional_text(item.get("parentObservationId"), "parentObservationId", max_bytes=512),
            type=_required_text(item.get("type", "SPAN"), "type", max_bytes=128).lower(),
            name=_required_text(item.get("name"), "name"),
            started_at=_datetime(item.get("startTime"), "startTime"),
            ended_at=_optional_datetime(item.get("endTime"), "endTime"),
            status=None,
            level=level.lower() if level is not None else None,
            status_message=_optional_text(item.get("statusMessage"), "statusMessage")
            if item.get("statusMessage")
            else None,
            model=ModelIdentity(requested=requested, response=response)
            if requested is not None or response is not None
            else None,
            usage=_usage(item.get("usageDetails")),
            cost_usd=_decimal(item.get("totalCost")),
            input=_io_value(item.get("input"), attributes.get("input.mime_type")) if view is TraceView.full else None,
            output=_io_value(item.get("output"), attributes.get("output.mime_type"))
            if view is TraceView.full
            else None,
            attributes=attributes,
            resource_attributes=_metadata_namespace(metadata, "resourceAttributes") or None,
            scope=InstrumentationScope.model_validate(
                {"name": scope.get("name"), "version": scope.get("version"), "attributes": scope.get("attributes")}
            )
            if scope
            else None,
            events=None,
            links=None,
        )
    except ValidationError as error:
        raise TraceQueryProviderError("malformed") from error
    return project_observation(observation, view)


def _base_filters() -> list[dict[str, Any]]:
    return [_filter("string", "name", "=", _ROOT_NAME), _filter("boolean", "isRootObservation", "=", True)]


def _scope_filters(organization_id: str, workspace_id: str) -> list[dict[str, Any]]:
    return [
        _metadata_filter("a13n.organization.id", organization_id),
        _metadata_filter("a13n.workspace.id", workspace_id),
    ]


def _history_filters(query: ProviderTraceRead) -> list[dict[str, Any]]:
    filters = [_filter("datetime", "startTime", "<", _format_datetime(query.to_started_at))]
    if query.history_from is not None:
        filters.append(_filter("datetime", "startTime", ">=", _format_datetime(query.history_from)))
    return filters


def _filter(kind: str, column: str, operator: str, value: object) -> dict[str, Any]:
    return {"type": kind, "column": column, "operator": operator, "value": value}


def _metadata_filter(key: str, value: str) -> dict[str, Any]:
    return {"type": "stringObject", "column": "metadata", "key": f"attributes.{key}", "operator": "=", "value": value}


def _matches_query_correlation(trace: Trace, query: ProviderTraceQuery) -> bool:
    correlation = trace.correlation
    return (
        correlation.organization_id == query.organization_id
        and correlation.workspace_id == query.workspace_id
        and (query.thread_id is None or correlation.thread_id == query.thread_id)
        and (query.run_id is None or correlation.run_id == query.run_id)
        and (query.run_attempt_id is None or correlation.run_attempt_id == query.run_attempt_id)
    )


def _page(payload: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], str | None]:
    data, meta = payload.get("data"), payload.get("meta")
    if not isinstance(data, list) or not isinstance(meta, Mapping):
        raise TraceQueryProviderError("malformed")
    items: list[Mapping[str, Any]] = []
    for item in data:
        if not isinstance(item, Mapping):
            raise TraceQueryProviderError("malformed")
        items.append(cast(Mapping[str, Any], item))
    cursor = _optional_text(meta.get("cursor"), "cursor", max_bytes=4096)
    return items, cursor


def _is_run_attempt_root(item: Mapping[str, Any]) -> bool:
    return item.get("name") == _ROOT_NAME and item.get("parentObservationId") is None


def _metadata(value: object) -> Mapping[str, JsonValue]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TraceQueryProviderError("malformed")
    _bounded_json(value, max_bytes=_MAX_METADATA_BYTES)
    if len(value) > _MAX_METADATA_ENTRIES:
        raise TraceQueryProviderError("response_too_large")
    return cast(Mapping[str, JsonValue], value)


def _otel_attributes(metadata: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return _metadata_namespace(metadata, "attributes")


def _metadata_namespace(metadata: Mapping[str, JsonValue], namespace: str) -> dict[str, JsonValue]:
    normalized: dict[str, JsonValue] = {}
    nested = metadata.get(namespace)
    if nested is not None:
        if not isinstance(nested, Mapping):
            raise TraceQueryProviderError("malformed")
        normalized.update(cast(Mapping[str, JsonValue], nested))
    prefix = f"{namespace}."
    for key, value in metadata.items():
        if not key.startswith(prefix):
            continue
        nested_key = key[len(prefix) :]
        if not nested_key or nested_key in normalized:
            raise TraceQueryProviderError("malformed")
        normalized[nested_key] = value
    if len(normalized) > _MAX_METADATA_ENTRIES:
        raise TraceQueryProviderError("response_too_large")
    return normalized


def _attribute(attributes: Mapping[str, JsonValue], key: str) -> str:
    return _required_text(attributes.get(key), key)


def _validate_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Langfuse base URL is invalid")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def validate_langfuse_base_url(value: str) -> str:
    """Validate and normalize the deployment-owned Langfuse origin."""
    return _validate_base_url(value)
