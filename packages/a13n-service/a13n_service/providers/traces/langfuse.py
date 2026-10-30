"""Langfuse: spans exported over its OTLP endpoint and read back through the public Observations API (v2)."""

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import ClassVar, Self
from urllib.parse import quote

from pydantic import BaseModel, Field, JsonValue, SecretStr, ValidationError

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
    rank,
    summary,
)

MAX_SUMMARY_BYTES = 8 * 1024 * 1024
MAX_ROOTS = 5_000
MAX_PAGES = 500
METRIC_BATCH = 100
METRIC_ROWS = 1_000

_OBSERVATIONS = "/api/public/v2/observations"
_FIELDS = "core,basic,io,metadata,model,usage,metrics"
_OVERVIEW_FIELDS = "core,basic,metadata,model,usage"
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

    @property
    def query_identity(self) -> str:
        return cursors.query_owner(self.type, self.url, self.public_key, self.secret_key)

    async def search_traces(self, query: SearchQuery, owner: str) -> TraceSearchPage:
        owner = query.page_owner(owner)
        position = (
            SearchPosition.read(cursors.decode(query.cursor, "langfuse-search-keyset", owner), query)
            if query.cursor
            else None
        )
        collected_at = datetime.now(UTC)
        items = await self._collect_summaries(query)
        needs_all_costs = query.order_by == "cost" or query.min_cost_usd is not None or query.max_cost_usd is not None
        if needs_all_costs:
            items = await self._with_costs(items, query, collected_at)
        items = rank(items, query)
        remaining = [item for item in items if position is None or position.precedes(item, query)]
        page = remaining[: query.roots.limit]
        if not needs_all_costs:
            # Display-only costs cannot affect selection: aggregate only after ranking and paging.
            page = await self._with_costs(page, query, collected_at)
        return TraceSearchPage(
            items=page,
            total=len(items),
            collected_at=collected_at,
            next_cursor=cursors.encode(
                "langfuse-search-keyset", owner, *SearchPosition.from_item(page[-1], query).values()
            )
            if len(remaining) > len(page)
            else None,
        )

    async def _collect_summaries(self, query: SearchQuery) -> list[TraceSearchItem]:
        roots: dict[str, Span] = {}
        positions: set[str] = set()
        root_query = replace(query.roots, limit=100)
        read_count, size = 0, 0
        while True:
            page = await self.query(root_query)
            read_count += len(page.items)
            if read_count > MAX_ROOTS:
                raise _too_large()
            for span in page.items:
                if matches_root(span, query.roots):
                    root = summary(span)
                    previous = roots.get(root.trace_id)
                    size += len(root.model_dump_json().encode()) - (
                        len(previous.model_dump_json().encode()) if previous else 0
                    )
                    if size > MAX_SUMMARY_BYTES:
                        raise _too_large()
                    roots[root.trace_id] = root
            if page.next_cursor is None:
                break
            if page.next_cursor in positions or len(positions) >= MAX_PAGES:
                raise unavailable(self.type)
            positions.add(page.next_cursor)
            root_query = replace(root_query, cursor=page.next_cursor)
        return [
            TraceSearchItem(
                root=root,
                duration_seconds=Decimal(str((root.ended_at - root.started_at).total_seconds()))
                if root.ended_at
                else None,
                cost_usd=None,
            )
            for root in roots.values()
        ]

    async def _with_costs(
        self, items: list[TraceSearchItem], query: SearchQuery, collected_at: datetime
    ) -> list[TraceSearchItem]:
        totals: dict[str, Decimal] = {}
        budget = _MetricBudget()
        for offset in range(0, len(items), METRIC_BATCH):
            batch = items[offset : offset + METRIC_BATCH]
            totals.update(
                await self._costs(
                    [item.root.trace_id for item in batch],
                    query.correlation,
                    min(item.root.started_at for item in batch) - timedelta(minutes=1),
                    collected_at + timedelta(minutes=1),
                    budget=budget,
                )
            )
        return [item.model_copy(update={"cost_usd": totals.get(item.root.trace_id)}) for item in items]

    async def _costs(
        self,
        ids: list[str],
        correlation: Mapping[str, str],
        after: datetime,
        before: datetime,
        *,
        budget: "_MetricBudget | None" = None,
    ) -> dict[str, Decimal]:
        budget = budget or _MetricBudget()
        budget.remaining -= 1
        if budget.remaining < 0:
            raise _too_large()
        # Expanding cost categories preserves missing cost vs an explicit zero. SUM(totalCost) alone
        # cannot: events_core's map lookup defaults a missing total to zero. Never add root cost again.
        filters: list[dict[str, JsonValue]] = [
            {"type": "stringOptions", "column": "traceId", "operator": "any of", "value": list(ids)},
            *(
                {
                    "type": "stringObject",
                    "column": "metadata",
                    "key": f"attributes.{key}",
                    "operator": "=",
                    "value": value,
                }
                for key, value in correlation.items()
            ),
        ]
        request: dict[str, JsonValue] = {
            "view": "observations",
            "dimensions": [{"field": "traceId"}, {"field": "costType"}],
            "metrics": [{"measure": "costByType", "aggregation": "sum"}],
            "filters": list(filters),
            "fromTimestamp": format_time(after),
            "toTimestamp": format_time(before),
            "orderBy": [{"field": "sum_costByType", "direction": "desc"}],
            "config": {"row_limit": METRIC_ROWS},
        }
        response = await read_json(
            self.type,
            "GET",
            self.url + "/api/public/v2/metrics",
            timeout=self.timeout,
            headers=self._auth_headers,
            params={"query": json.dumps(request, separators=(",", ":"))},
        )
        try:
            rows = _Metrics.model_validate(response).data
        except ValidationError as error:
            raise unavailable(self.type) from error
        if len(rows) >= METRIC_ROWS:
            # Cost categories add groups. A full response may be truncated; recursively shrink the
            # candidate set rather than treating a top-N response as complete, even for zero totals.
            if len(ids) <= 1:
                raise _too_large()
            middle = len(ids) // 2
            first = await self._costs(ids[:middle], correlation, after, before, budget=budget)
            second = await self._costs(ids[middle:], correlation, after, before, budget=budget)
            return {**first, **second}
        totals: dict[str, Decimal] = {}
        seen: set[tuple[str, str]] = set()
        for row in rows:
            identity = (row.traceId, row.costType)
            if row.traceId not in ids or identity in seen or not row.sum_costByType.is_finite():
                raise unavailable(self.type)
            seen.add(identity)
            if row.costType == "total":
                totals[row.traceId] = row.sum_costByType
        return totals

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
        # Structured filters take precedence over fixed query parameters in v2.
        for column, value in (("traceId", query.trace_id), ("id", query.span_id)):
            if value is not None:
                filters.append({"type": "string", "column": column, "operator": "=", "value": value})
        params = {
            "fields": "core,basic,metadata,usage"
            if query.metrics_only
            else (_FIELDS if query.include_content else _OVERVIEW_FIELDS),
            "expandMetadata": _EXPANDED if query.include_content else "attributes",
            "limit": str(query.limit),
            "filter": json.dumps(filters, separators=(",", ":")),
        }
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


class _Cost(BaseModel):
    traceId: str
    costType: str
    sum_costByType: Decimal


class _Metrics(BaseModel):
    data: list[_Cost]


def _too_large() -> ServiceError:
    return ServiceError("payload_too_large", "Too many trace summaries to rank completely. Narrow the time range.")


@dataclass
class _MetricBudget:
    remaining: int = MAX_PAGES
