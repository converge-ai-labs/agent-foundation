"""Langfuse: spans exported over its OTLP endpoint and read back through the public Observations API (v2)."""

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar, Self
from urllib.parse import quote

from pydantic import BaseModel, Field, JsonValue, SecretStr, ValidationError

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

_OBSERVATIONS = "/api/public/v2/observations"
_FIELDS = "core,basic,io,metadata,model,usage,metrics"
# The metadata namespaces holding the span's OTel attributes, resource attributes and instrumentation scope.
_EXPANDED = "attributes,resourceAttributes,scope"


class _Meta(BaseModel):
    cursor: str | None = Field(default=None, max_length=1024)


class _Page(BaseModel):
    data: list[dict[str, JsonValue]]
    meta: _Meta


@dataclass(frozen=True, slots=True)
class Langfuse:
    """One Langfuse project: its key pair both ingests spans and reads them back."""

    type: ClassVar[TraceBackendType] = "langfuse"
    url: str
    public_key: str
    secret_key: str = field(repr=False)
    timeout: float  # bounds each query

    @classmethod
    def configure(
        cls,
        url: str | None,
        public_key: str | None,
        secret_key: SecretStr | None,
        *,
        timeout: float,
    ) -> Self:
        if url is None or public_key is None or secret_key is None:
            raise ValueError("telemetry: Langfuse needs trace_url, langfuse_public_key and langfuse_secret_key")
        return cls(url, public_key, secret_key.get_secret_value(), timeout)

    @property
    def otlp_endpoint(self) -> str:
        return f"{self.url}/api/public/otel/v1/traces"

    @property
    def otlp_headers(self) -> dict[str, str]:
        # Opt into real-time ingestion for the v2 read APIs.
        return {**self._auth_headers, "x-langfuse-ingestion-version": "4"}

    @property
    def _auth_headers(self) -> dict[str, str]:
        pair = base64.b64encode(f"{self.public_key}:{self.secret_key}".encode()).decode()
        return {"Authorization": f"Basic {pair}"}

    async def query(self, query: SpanQuery) -> SpanPage:
        filters: list[dict[str, JsonValue]] = [
            {"type": "datetime", "column": "startTime", "operator": ">=", "value": format_time(query.started_after)},
            {"type": "datetime", "column": "startTime", "operator": "<", "value": format_time(query.started_before)},
        ]
        for key, value in query.attributes.items():
            # Langfuse keeps OTel span attributes under `metadata.attributes`.
            filters.append(
                {
                    "type": "stringObject",
                    "column": "metadata",
                    "key": f"attributes.{key}",
                    "operator": "=",
                    "value": value,
                }
            )
        if query.roots:
            filters.append({"type": "boolean", "column": "isRootObservation", "operator": "=", "value": True})
        params = {
            "fields": _FIELDS,
            "expandMetadata": _EXPANDED,
            "limit": str(query.limit),
            "filter": json.dumps(filters, separators=(",", ":")),
        }
        if query.trace_id is not None:
            params["traceId"] = query.trace_id
        if query.cursor is not None:
            params["cursor"] = query.cursor
        # The key pair that ingests spans also reads them.
        body = await read_json(
            self.type,
            "GET",
            self.url + _OBSERVATIONS,
            timeout=self.timeout,
            headers=self._auth_headers,
            params=params,
        )
        try:
            page = _Page.model_validate(body)
            return SpanPage(items=[self._span(row) for row in page.data], next_cursor=page.meta.cursor)
        except ValidationError as error:
            raise unavailable(self.type) from error

    def _span(self, row: Mapping[str, JsonValue]) -> Span:
        metadata, level = row.get("metadata"), row.get("level")
        scope = _namespace(metadata, "scope")
        return Span.model_validate(
            {
                "trace_id": row.get("traceId"),
                "id": row.get("id"),
                "parent_id": row.get("parentObservationId"),
                "name": row.get("name"),
                "kind": str(row.get("type") or "span").lower(),
                "started_at": row.get("startTime"),
                "ended_at": row.get("endTime"),
                "status": "error" if level == "ERROR" else "ok",
                "status_message": row.get("statusMessage"),
                "level": level.lower() if isinstance(level, str) else None,
                "model": row.get("providedModelName") or row.get("model"),
                "usage": row.get("usageDetails") or {},
                "cost_usd": row.get("totalCost"),
                "input": parse_json_text(row.get("input")),
                "output": parse_json_text(row.get("output")),
                "attributes": _namespace(metadata, "attributes"),
                "resource_attributes": _namespace(metadata, "resourceAttributes"),
                "scope": {"name": scope["name"], "version": scope.get("version")} if "name" in scope else None,
                # The Observations API returns neither span events nor links.
                "events": [],
                "links": [],
                "source_url": self._source_url(row),
            }
        )

    def _source_url(self, row: Mapping[str, JsonValue]) -> str | None:
        """The trace in the Langfuse UI, which shares the API origin; settings refuse credentials in that URL."""
        project, trace = row.get("projectId"), row.get("traceId")
        if not isinstance(project, str) or not isinstance(trace, str):
            return None
        return f"{self.url}/project/{quote(project, safe='')}/traces/{quote(trace, safe='')}"


def _namespace(metadata: JsonValue, name: str) -> dict[str, JsonValue]:
    """One OTel map Langfuse keeps in the metadata, nested under `name` or flattened as `name.<key>`."""
    if not isinstance(metadata, dict):
        return {}
    values = parse_json_map(metadata.get(name))
    prefix = f"{name}."
    values.update({key.removeprefix(prefix): value for key, value in metadata.items() if key.startswith(prefix)})
    return values
