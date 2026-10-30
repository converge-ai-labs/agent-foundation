"""Authorize a whole-window metric query and delegate execution to the trace provider."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal, Self

import anyio
from pydantic import BaseModel, ConfigDict, Field, model_validator

from a13n_service.infra import cursors
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import ObjectId
from a13n_service.infra.telemetry import correlation_attributes
from a13n_service.providers.traces import SpanQuery, unavailable
from a13n_service.providers.traces.search import SearchQuery, TraceSearchPage, matches_root
from a13n_service.runs import traces
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.authorize import Principal

COLLECTION_SECONDS = 30
Nonnegative = Annotated[Decimal, Field(ge=0, allow_inf_nan=False, max_digits=30, decimal_places=15)]


class TraceSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: ObjectId | None = None
    thread_id: ObjectId | None = None
    run_id: ObjectId | None = None
    attribute: list[str] = Field(default_factory=list, max_length=8)
    started_after: datetime
    started_before: datetime
    order_by: Literal["started", "duration", "cost"] = "started"
    direction: Literal["asc", "desc"] = "desc"
    min_duration_seconds: Nonnegative | None = None
    max_duration_seconds: Nonnegative | None = None
    min_cost_usd: Nonnegative | None = None
    max_cost_usd: Nonnegative | None = None
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = None

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if not self.started_after.tzinfo or not self.started_before.tzinfo:
            raise ValueError("The time range must include timezones")
        if not timedelta(0) < self.started_before - self.started_after <= traces.MAX_WINDOW:
            raise ValueError("The time range must span at most 31 days")
        for low, high in (
            (self.min_duration_seconds, self.max_duration_seconds),
            (self.min_cost_usd, self.max_cost_usd),
        ):
            if low is not None and high is not None and low > high:
                raise ValueError("Minimum must not exceed maximum")
        return self


async def search(runtime: Runtime, actor: Principal, workspace_id: str, query: TraceSearch) -> TraceSearchPage:
    scope = await traces._read_scope(runtime.storage, actor, workspace_id)
    provider = traces._available(runtime.traces)
    selected = traces._selected_attributes(query.attribute)
    correlation = correlation_attributes(
        scope.organization_id,
        scope.workspace_id,
        session_id=query.session_id,
        thread_id=query.thread_id,
        run_id=query.run_id,
    )
    roots = SpanQuery(
        {**selected, **correlation},
        query.started_after,
        query.started_before,
        query.limit,
        roots=True,
        metrics_only=True,
    )
    owner = cursors.query_owner(
        "trace-search-v3",
        scope.organization_id,
        scope.workspace_id,
        actor.id,
        provider.query_identity,
        sorted(roots.attributes.items()),
        roots.started_after,
        roots.started_before,
    )
    request = SearchQuery(
        roots=roots,
        correlation=correlation,
        order_by=query.order_by,
        direction=query.direction,
        min_duration_seconds=query.min_duration_seconds,
        max_duration_seconds=query.max_duration_seconds,
        min_cost_usd=query.min_cost_usd,
        max_cost_usd=query.max_cost_usd,
        cursor=query.cursor,
    )
    try:
        with anyio.fail_after(COLLECTION_SECONDS):
            page = await provider.search_traces(request, owner)
    except TimeoutError:
        raise ServiceError("request_timeout", "Trace search timed out. Narrow the time range and try again.") from None
    # Providers scope aggregate inputs; independently recheck every returned root before exposing its metrics.
    if any(not matches_root(item.root, roots) for item in page.items):
        raise unavailable(provider.type)
    return page
