"""Langfuse v4 Observations API v2 Trace Query adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast
from urllib.parse import quote, urlsplit, urlunsplit

import httpx2
from pydantic import JsonValue

from .domain import (
    ProviderObservation,
    ProviderTraceDetail,
    ProviderTracePage,
    ProviderTraceQuery,
    ProviderTraceSummary,
    SearchIn,
    TraceCorrelation,
    TraceQueryCapabilities,
    TraceView,
)
from .errors import TraceQueryProviderError

_OBSERVATIONS_PATH = "/api/public/v2/observations"
_ROOT_NAME = "foundation.run_attempt"
_FIELD_GROUPS = "core,basic,io,metadata,model,usage,metrics,trace_context"
_COMPACT_FIELD_GROUPS = "core,basic,metadata,model,usage,metrics,trace_context"
_EXPANDED_METADATA_KEYS = "attributes,resourceAttributes,scope"
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_OBSERVATIONS_PER_TRACE = 1000
_MAX_CONTENT_BYTES = 1024 * 1024
_MAX_METADATA_BYTES = 256 * 1024
_MAX_METADATA_ENTRIES = 512
_MAX_TEXT_BYTES = 4096
_MAX_USAGE_ENTRIES = 64


class LangfuseTraceQueryProvider:
    """Read normalized traces from documented Langfuse v4 public APIs."""

    def __init__(
        self,
        client: httpx2.AsyncClient,
        *,
        base_url: str,
        public_key: str,
        secret_key: str,
    ) -> None:
        self._client = client
        self._base_url = _validate_base_url(base_url)
        if not public_key or not secret_key:
            raise ValueError("Langfuse query credentials are required")
        self._auth = httpx2.BasicAuth(public_key, secret_key)

    @property
    def capabilities(self) -> TraceQueryCapabilities:
        return TraceQueryCapabilities(
            input_search=True,
            output_search=True,
            combined_input_output_search=False,
            usage=True,
            cost=True,
            source_url=True,
        )

    async def list_traces(self, query: ProviderTraceQuery) -> ProviderTracePage:
        if query.limit < 1 or query.limit > 100:
            raise ValueError("Provider trace limit must be between 1 and 100")
        filters = _base_filters()
        filters.extend(
            (
                _filter("datetime", "startTime", ">=", _format_datetime(query.from_started_at)),
                _filter("datetime", "startTime", "<", _format_datetime(query.to_started_at)),
                _metadata_filter("a13n.organization.id", query.organization_id),
                _metadata_filter("a13n.workspace.id", query.workspace_id),
            )
        )
        if query.thread_id is not None:
            filters.append(_filter("string", "sessionId", "=", query.thread_id))
        if query.run_id is not None:
            filters.append(_metadata_filter("a13n.foundation.run.id", query.run_id))
        if query.run_attempt_id is not None:
            filters.append(_metadata_filter("a13n.run_attempt.id", query.run_attempt_id))
        if query.query is not None:
            if query.search_in is SearchIn.input:
                filters.append(_filter("string", "input", "matches", query.query))
            elif query.search_in is SearchIn.output:
                filters.append(_filter("string", "output", "matches", query.query))
            else:
                raise TraceQueryProviderError("filter_unsupported")

        payload = await self._get_observations(
            {
                "fields": _FIELD_GROUPS,
                "expandMetadata": _EXPANDED_METADATA_KEYS,
                "limit": str(query.limit),
                "filter": json.dumps(filters, separators=(",", ":")),
                **({"cursor": query.cursor} if query.cursor is not None else {}),
            }
        )
        observations, cursor = _page(payload)
        if len(observations) > query.limit:
            raise TraceQueryProviderError("response_too_large")
        # Keep a local correlation check even though the same exact values are
        # forced into the backend filter; provider responses are never trusted.
        summaries = tuple(
            summary for item in observations if _matches_query_correlation(summary := self._summary(item), query)
        )
        return ProviderTracePage(summaries, cursor)

    async def get_trace(self, trace_id: str, view: TraceView) -> ProviderTraceDetail | None:
        _bounded_text(trace_id, "trace_id", max_bytes=512)
        observations: list[Mapping[str, Any]] = []
        cursor: str | None = None
        fields = _FIELD_GROUPS if view is TraceView.full else _COMPACT_FIELD_GROUPS
        while True:
            remaining = _MAX_OBSERVATIONS_PER_TRACE - len(observations)
            if remaining <= 0:
                raise TraceQueryProviderError("response_too_large")
            payload = await self._get_observations(
                {
                    "fields": fields,
                    "expandMetadata": _EXPANDED_METADATA_KEYS,
                    "limit": str(min(remaining, 1000)),
                    "traceId": trace_id,
                    **({"cursor": cursor} if cursor is not None else {}),
                }
            )
            page, cursor = _page(payload)
            observations.extend(page)
            if cursor is None:
                break
        roots = [item for item in observations if _is_run_attempt_root(item)]
        if not roots:
            return None
        if len(roots) != 1:
            raise TraceQueryProviderError("malformed")
        summary = self._summary(roots[0], observation_count=len(observations))
        normalized = tuple(_observation(item, include_content=view is TraceView.full) for item in observations)
        normalized = tuple(sorted(normalized, key=lambda item: (item.started_at, item.id)))
        return ProviderTraceDetail(summary, normalized)

    async def _get_observations(self, params: Mapping[str, str]) -> Mapping[str, Any]:
        try:
            async with self._client.stream(
                "GET",
                f"{self._base_url}{_OBSERVATIONS_PATH}",
                params=params,
                auth=self._auth,
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
        except TraceQueryProviderError:
            raise
        except httpx2.HTTPError as error:
            raise TraceQueryProviderError("unavailable") from error
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise TraceQueryProviderError("malformed") from error
        if not isinstance(value, Mapping):
            raise TraceQueryProviderError("malformed")
        return cast(Mapping[str, Any], value)

    def _summary(
        self,
        item: Mapping[str, Any],
        *,
        observation_count: int | None = None,
    ) -> ProviderTraceSummary:
        if not _is_run_attempt_root(item):
            raise TraceQueryProviderError("malformed")
        trace_id = _required_text(item.get("traceId"), "traceId", max_bytes=512)
        started_at = _datetime(item.get("startTime"), "startTime")
        ended_at = _optional_datetime(item.get("endTime"), "endTime")
        metadata = _metadata(item.get("metadata"))
        attributes = _otel_attributes(metadata)
        correlation = TraceCorrelation(
            organization_id=_attribute(attributes, "a13n.organization.id"),
            workspace_id=_attribute(attributes, "a13n.workspace.id"),
            session_id=_attribute(attributes, "a13n.observation.session.id"),
            thread_id=_attribute(attributes, "a13n.thread.id"),
            run_id=_attribute(attributes, "a13n.foundation.run.id"),
            run_attempt_id=_attribute(attributes, "a13n.run_attempt.id"),
            agent_id=_attribute(attributes, "a13n.agent.preset.id"),
        )
        return ProviderTraceSummary(
            id=trace_id,
            name=_required_text(item.get("name"), "name"),
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=_duration_ms(started_at, ended_at),
            trace_status=_status(item.get("level"), ended_at),
            correlation=correlation,
            input=_io_value(item.get("input"), attributes.get("input.mime_type")),
            output=_io_value(item.get("output"), attributes.get("output.mime_type")),
            observation_count=observation_count,
            models=_models(item),
            usage=_usage(item.get("usageDetails")),
            total_cost_usd=_decimal(item.get("totalCost")),
            source_url=self._source_url(item, trace_id),
        )

    def _source_url(self, item: Mapping[str, Any], trace_id: str) -> str:
        project_id = _required_text(item.get("projectId"), "projectId", max_bytes=512)
        return f"{self._base_url}/project/{quote(project_id, safe='')}/traces/{quote(trace_id, safe='')}"


def _observation(item: Mapping[str, Any], *, include_content: bool) -> ProviderObservation:
    started_at = _datetime(item.get("startTime"), "startTime")
    ended_at = _optional_datetime(item.get("endTime"), "endTime")
    metadata = _metadata(item.get("metadata"))
    attributes = _otel_attributes(metadata)
    raw_type = item.get("type")
    observation_type = {
        "SPAN": "span",
        "GENERATION": "generation",
        "EVENT": "event",
    }.get(raw_type if isinstance(raw_type, str) else "", "unknown")
    return ProviderObservation(
        id=_required_text(item.get("id"), "id", max_bytes=512),
        parent_id=_optional_text(item.get("parentObservationId"), "parentObservationId", max_bytes=512),
        type=cast(Any, observation_type),
        name=_required_text(item.get("name"), "name"),
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=_duration_ms(started_at, ended_at),
        status=_status(item.get("level"), ended_at),
        model=_model(item),
        usage=_usage(item.get("usageDetails")),
        cost_usd=_decimal(item.get("totalCost")),
        input=_io_value(item.get("input"), attributes.get("input.mime_type")) if include_content else None,
        output=_io_value(item.get("output"), attributes.get("output.mime_type")) if include_content else None,
        metadata=_normalized_metadata(metadata) if include_content else {},
    )


def _base_filters() -> list[dict[str, Any]]:
    return [
        _filter("string", "name", "=", _ROOT_NAME),
        _filter("boolean", "isRootObservation", "=", True),
    ]


def _filter(kind: str, column: str, operator: str, value: object) -> dict[str, Any]:
    return {"type": kind, "column": column, "operator": operator, "value": value}


def _metadata_filter(key: str, value: str) -> dict[str, Any]:
    return {
        "type": "stringObject",
        "column": "metadata",
        "key": f"attributes.{key}",
        "operator": "=",
        "value": value,
    }


def _matches_query_correlation(summary: ProviderTraceSummary, query: ProviderTraceQuery) -> bool:
    correlation = summary.correlation
    return (
        correlation.organization_id == query.organization_id
        and correlation.workspace_id == query.workspace_id
        and (query.thread_id is None or correlation.thread_id == query.thread_id)
        and (query.run_id is None or correlation.run_id == query.run_id)
        and (query.run_attempt_id is None or correlation.run_attempt_id == query.run_attempt_id)
    )


def _page(payload: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], str | None]:
    data = payload.get("data")
    meta = payload.get("meta")
    if not isinstance(data, list) or not isinstance(meta, Mapping):
        raise TraceQueryProviderError("malformed")
    items: list[Mapping[str, Any]] = []
    for item in data:
        if not isinstance(item, Mapping):
            raise TraceQueryProviderError("malformed")
        items.append(cast(Mapping[str, Any], item))
    cursor = meta.get("cursor")
    if cursor is not None:
        cursor = _required_text(cursor, "cursor", max_bytes=4096)
    return items, cast(str | None, cursor)


def _is_run_attempt_root(item: Mapping[str, Any]) -> bool:
    return (
        item.get("name") == _ROOT_NAME
        and item.get("parentObservationId") is None
        and item.get("isRootObservation") is True
    )


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
    attributes = _metadata_namespace(metadata, "attributes")
    return attributes or metadata


def _normalized_metadata(metadata: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    normalized: dict[str, JsonValue] = {}
    for source, target in (
        ("attributes", "attributes"),
        ("resourceAttributes", "resource_attributes"),
        ("scope", "scope"),
    ):
        value = _metadata_namespace(metadata, source)
        if value:
            normalized[target] = cast(JsonValue, value)
    _bounded_json(normalized, max_bytes=_MAX_METADATA_BYTES)
    return normalized


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


def _io_value(value: object, mime_type: object) -> JsonValue | None:
    if value is None:
        return None
    if isinstance(value, str):
        if len(value.encode("utf-8")) > _MAX_CONTENT_BYTES:
            raise TraceQueryProviderError("response_too_large")
        if mime_type == "application/json":
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, RecursionError) as error:
                raise TraceQueryProviderError("malformed") from error
            _bounded_json(parsed, max_bytes=_MAX_CONTENT_BYTES)
            return cast(JsonValue, parsed)
        return value
    _bounded_json(value, max_bytes=_MAX_CONTENT_BYTES)
    return cast(JsonValue, value)


def _usage(value: object) -> Mapping[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or len(value) > _MAX_USAGE_ENTRIES:
        raise TraceQueryProviderError("malformed")
    normalized: dict[str, int] = {}
    for key, amount in value.items():
        name = _required_text(key, "usage key", max_bytes=128)
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            raise TraceQueryProviderError("malformed")
        normalized[name] = amount
    return normalized or None


def _models(item: Mapping[str, Any]) -> tuple[str, ...]:
    value = _model(item)
    return (value,) if value is not None else ()


def _model(item: Mapping[str, Any]) -> str | None:
    for field in ("model", "providedModelName"):
        value = item.get(field)
        if value in (None, ""):
            continue
        return _required_text(value, field)
    return None


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise TraceQueryProviderError("malformed")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise TraceQueryProviderError("malformed") from error
    if not result.is_finite() or result < 0:
        raise TraceQueryProviderError("malformed")
    return result


def _status(level: object, ended_at: datetime | None):
    if level == "ERROR":
        return "error"
    return "ok" if ended_at is not None else "unset"


def _duration_ms(started_at: datetime, ended_at: datetime | None) -> int | None:
    if ended_at is None:
        return None
    duration = int((ended_at - started_at).total_seconds() * 1000)
    if duration < 0:
        raise TraceQueryProviderError("malformed")
    return duration


def _datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise TraceQueryProviderError("malformed")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TraceQueryProviderError("malformed") from error
    if parsed.tzinfo is None:
        raise TraceQueryProviderError("malformed")
    return parsed.astimezone(UTC)


def _optional_datetime(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    return _datetime(value, field)


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Trace query timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _required_text(value: object, field: str, *, max_bytes: int = _MAX_TEXT_BYTES) -> str:
    if not isinstance(value, str) or not value:
        raise TraceQueryProviderError("malformed")
    _bounded_text(value, field, max_bytes=max_bytes)
    return value


def _optional_text(value: object, field: str, *, max_bytes: int = _MAX_TEXT_BYTES) -> str | None:
    if value is None:
        return None
    return _required_text(value, field, max_bytes=max_bytes)


def _bounded_text(value: str, field: str, *, max_bytes: int = _MAX_TEXT_BYTES) -> None:
    if not isinstance(value, str) or "\x00" in value:
        raise TraceQueryProviderError("malformed")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise TraceQueryProviderError("malformed") from error
    if len(encoded) > max_bytes:
        raise TraceQueryProviderError("response_too_large")


def _bounded_json(value: object, *, max_bytes: int) -> None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as error:
        raise TraceQueryProviderError("malformed") from error
    if len(encoded) > max_bytes:
        raise TraceQueryProviderError("response_too_large")


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
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def validate_langfuse_base_url(value: str) -> str:
    """Validate and normalize the deployment-owned Langfuse origin."""

    return _validate_base_url(value)
