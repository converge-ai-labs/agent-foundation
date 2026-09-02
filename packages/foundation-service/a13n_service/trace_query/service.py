"""Authorized provider-neutral Trace Query orchestration."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol
from urllib.parse import urlsplit

from a13n_service.iam import AuthenticatedActor

from .cursors import TraceCursorError, decode_trace_cursor, encode_trace_cursor
from .domain import (
    Observation,
    ProviderObservation,
    ProviderTraceDetail,
    ProviderTracePage,
    ProviderTraceQuery,
    ProviderTraceSummary,
    SearchIn,
    TraceCollection,
    TraceCorrelation,
    TraceDetail,
    TraceSummary,
    TraceView,
)
from .errors import TraceQueryError, TraceQueryProviderError
from .provider import TraceQueryProvider

_MAX_QUERY_BYTES = 512
_MAX_RANGE = timedelta(days=31)
_DEFAULT_RANGE = timedelta(hours=24)
_MAX_PROVIDER_TEXT_BYTES = 4096
_MAX_PROVIDER_ID_BYTES = 1024
_MAX_PROVIDER_CONTENT_BYTES = 1024 * 1024
_MAX_PROVIDER_METADATA_BYTES = 256 * 1024
_MAX_PROVIDER_METADATA_ENTRIES = 512
_MAX_PROVIDER_USAGE_ENTRIES = 64
_MAX_PROVIDER_MODELS = 64
_MAX_PROVIDER_OBSERVATIONS = 1000
_MAX_PROVIDER_CURSOR_BYTES = 4096


@dataclass(frozen=True, slots=True)
class TraceQueryScope:
    organization_id: str
    workspace_id: str


@dataclass(frozen=True, slots=True)
class AuthorizedRunAttempt:
    run_attempt_id: str
    number: int
    outcome: Literal["succeeded", "yielded", "failed", "cancelled"] | None


class TraceAccessAuthorizer(Protocol):
    """Run-domain authorization port; implementations own their short sessions."""

    async def resolve_scope(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
    ) -> TraceQueryScope: ...

    async def authorize_run_attempts(
        self,
        *,
        actor: AuthenticatedActor,
        scope: TraceQueryScope,
        correlations: Sequence[TraceCorrelation],
    ) -> Mapping[str, AuthorizedRunAttempt]: ...


class TraceQueryService:
    def __init__(
        self,
        *,
        provider_key: str,
        provider: TraceQueryProvider | None,
        authorizer: TraceAccessAuthorizer | None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider_key = provider_key
        self._provider = provider
        self._authorizer = authorizer
        self._clock = clock or (lambda: datetime.now(UTC))

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        from_started_at: datetime | None,
        to_started_at: datetime | None,
        limit: int,
        cursor: str | None,
        query: str | None,
        search_in: SearchIn | None,
        thread_id: str | None,
        run_id: str | None,
        run_attempt_id: str | None,
    ) -> TraceCollection:
        provider, authorizer = self._require_available()
        _validate_search(query, search_in)
        _validate_exact_filters(thread_id, run_id, run_attempt_id)
        _validate_range_pair(from_started_at, to_started_at)
        scope = await authorizer.resolve_scope(actor=actor, workspace_id=workspace_id)
        cursor_scope = _cursor_scope(
            actor=actor,
            provider_key=self._provider_key,
            scope=scope,
            limit=limit,
            query=query,
            search_in=search_in,
            thread_id=thread_id,
            run_id=run_id,
            run_attempt_id=run_attempt_id,
        )
        try:
            decoded_cursor = decode_trace_cursor(cursor, scope=cursor_scope) if cursor is not None else None
        except TraceCursorError as error:
            raise TraceQueryError("invalid_cursor", "The collection cursor is invalid.", status_code=400) from error
        if decoded_cursor is None:
            start, end = _range(from_started_at, to_started_at, self._clock())
            provider_cursor = None
        else:
            cursor_start, cursor_end = _range(
                decoded_cursor.from_started_at,
                decoded_cursor.to_started_at,
                self._clock(),
            )
            if from_started_at is None:
                start, end = cursor_start, cursor_end
            else:
                requested_start, requested_end = _range(from_started_at, to_started_at, self._clock())
                if (requested_start, requested_end) != (cursor_start, cursor_end):
                    raise TraceQueryError("invalid_cursor", "The collection cursor is invalid.", status_code=400)
                start, end = requested_start, requested_end
            provider_cursor = decoded_cursor.provider_cursor
        provider_query = ProviderTraceQuery(
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            from_started_at=start,
            to_started_at=end,
            limit=limit,
            cursor=provider_cursor,
            query=query,
            search_in=search_in,
            thread_id=thread_id,
            run_id=run_id,
            run_attempt_id=run_attempt_id,
        )
        _require_capabilities(provider, provider_query)
        try:
            page = _validate_provider_page(await provider.list_traces(provider_query), limit=limit)
        except TraceQueryProviderError as error:
            raise _provider_error(error) from error
        candidates = _unique_candidates(
            item
            for item in page.items
            if item.correlation.organization_id == scope.organization_id
            and item.correlation.workspace_id == scope.workspace_id
        )
        authorized = await authorizer.authorize_run_attempts(
            actor=actor,
            scope=scope,
            correlations=tuple(item.correlation for item in candidates),
        )
        items = tuple(
            sorted(
                (
                    _public_summary(item, decision)
                    for item in candidates
                    if (decision := authorized.get(item.correlation.run_attempt_id)) is not None
                ),
                key=lambda item: (item.started_at, item.id),
                reverse=True,
            )
        )
        try:
            next_cursor = (
                encode_trace_cursor(
                    provider_cursor=page.next_cursor,
                    scope=cursor_scope,
                    from_started_at=start,
                    to_started_at=end,
                )
                if page.next_cursor is not None
                else None
            )
        except TraceCursorError as error:
            raise TraceQueryError(
                "trace_query_unavailable",
                "Trace Query is temporarily unavailable.",
                status_code=503,
            ) from error
        return TraceCollection(items=items, next_cursor=next_cursor)

    async def get(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        trace_id: str,
        view: TraceView,
    ) -> TraceDetail:
        provider, authorizer = self._require_available()
        _validate_trace_id(trace_id)
        scope = await authorizer.resolve_scope(actor=actor, workspace_id=workspace_id)
        try:
            detail = _validate_provider_detail(await provider.get_trace(trace_id, view), trace_id=trace_id)
        except TraceQueryProviderError as error:
            raise _provider_error(error) from error
        if (
            detail is None
            or detail.trace.correlation.organization_id != scope.organization_id
            or detail.trace.correlation.workspace_id != scope.workspace_id
        ):
            raise _not_found()
        authorized = await authorizer.authorize_run_attempts(
            actor=actor,
            scope=scope,
            correlations=(detail.trace.correlation,),
        )
        decision = authorized.get(detail.trace.correlation.run_attempt_id)
        if decision is None:
            raise _not_found()
        return TraceDetail(
            trace=_public_summary(detail.trace, decision),
            observations=tuple(_public_observation(item) for item in detail.observations),
        )

    def _require_available(self) -> tuple[TraceQueryProvider, TraceAccessAuthorizer]:
        if self._provider is None or self._authorizer is None:
            raise TraceQueryError(
                "trace_query_unavailable",
                "Trace Query is unavailable.",
                status_code=503,
            )
        return self._provider, self._authorizer


def _validate_range_pair(start: datetime | None, end: datetime | None) -> None:
    if (start is None) != (end is None):
        raise TraceQueryError(
            "invalid_request",
            "Trace Query requires both from and to.",
            status_code=400,
            details={"fields": ["from", "to"]},
        )


def _range(start: datetime | None, end: datetime | None, now: datetime) -> tuple[datetime, datetime]:
    _validate_range_pair(start, end)
    if start is None or end is None:
        normalized_end = _as_utc(now)
        return normalized_end - _DEFAULT_RANGE, normalized_end
    normalized_start = _as_utc(start)
    normalized_end = _as_utc(end)
    if normalized_start >= normalized_end or normalized_end - normalized_start > _MAX_RANGE:
        raise TraceQueryError(
            "invalid_request",
            "The Trace Query time range is invalid.",
            status_code=400,
            details={"fields": ["from", "to"]},
        )
    return normalized_start, normalized_end


def _validate_search(query: str | None, search_in: SearchIn | None) -> None:
    if (query is None) != (search_in is None):
        raise TraceQueryError(
            "invalid_request",
            "query and search_in must be supplied together.",
            status_code=400,
            details={"fields": ["query", "search_in"]},
        )
    if query is not None:
        if not _valid_bounded_text(query, max_bytes=_MAX_QUERY_BYTES):
            raise TraceQueryError(
                "invalid_request",
                "The Trace Query search value is invalid.",
                status_code=400,
                details={"fields": ["query"]},
            )


def _validate_exact_filters(*values: str | None) -> None:
    if any(value is not None and not _valid_bounded_text(value, max_bytes=1024) for value in values):
        raise TraceQueryError(
            "invalid_request",
            "A Trace Query correlation filter is invalid.",
            status_code=400,
            details={"fields": ["thread_id", "run_id", "run_attempt_id"]},
        )


def _validate_trace_id(value: str) -> None:
    if not _valid_bounded_text(value, max_bytes=512):
        raise TraceQueryError(
            "invalid_request",
            "The Trace identifier is invalid.",
            status_code=400,
            details={"fields": ["trace_id"]},
        )


def _valid_bounded_text(value: str, *, max_bytes: int) -> bool:
    if not value or "\x00" in value:
        return False
    try:
        return len(value.encode("utf-8")) <= max_bytes
    except UnicodeEncodeError:
        return False


def _require_capabilities(provider: TraceQueryProvider, query: ProviderTraceQuery) -> None:
    if query.query is None:
        return
    capabilities = provider.capabilities
    supported = (
        (query.search_in is SearchIn.input and capabilities.input_search)
        or (query.search_in is SearchIn.output and capabilities.output_search)
        or (query.search_in is SearchIn.input_output and capabilities.combined_input_output_search)
    )
    if not supported:
        raise TraceQueryError(
            "trace_query_filter_unsupported",
            "The selected Trace Query provider does not support this filter.",
            status_code=400,
        )


def _public_summary(item: ProviderTraceSummary, decision: AuthorizedRunAttempt) -> TraceSummary:
    if (
        decision.run_attempt_id != item.correlation.run_attempt_id
        or not isinstance(decision.number, int)
        or isinstance(decision.number, bool)
        or decision.number < 1
        or decision.outcome not in {None, "succeeded", "yielded", "failed", "cancelled"}
    ):
        raise TraceQueryError(
            "trace_query_unavailable",
            "Trace Query is temporarily unavailable.",
            status_code=503,
        )
    return TraceSummary(
        id=item.id,
        name=item.name,
        started_at=item.started_at,
        ended_at=item.ended_at,
        duration_ms=item.duration_ms,
        trace_status=item.trace_status,
        session_id=item.correlation.session_id,
        thread_id=item.correlation.thread_id,
        run_id=item.correlation.run_id,
        run_attempt_id=item.correlation.run_attempt_id,
        run_attempt_number=decision.number,
        run_attempt_outcome=decision.outcome,
        input=item.input,
        output=item.output,
        observation_count=item.observation_count,
        models=item.models,
        usage=item.usage,
        total_cost_usd=item.total_cost_usd,
        source_url=item.source_url,
    )


def _unique_candidates(items: Iterable[ProviderTraceSummary]) -> tuple[ProviderTraceSummary, ...]:
    candidates = tuple(items)
    trace_ids = {item.id for item in candidates}
    attempt_ids = {item.correlation.run_attempt_id for item in candidates}
    if len(trace_ids) != len(candidates) or len(attempt_ids) != len(candidates):
        raise TraceQueryError(
            "trace_query_unavailable",
            "Trace Query is temporarily unavailable.",
            status_code=503,
        )
    return candidates


def _public_observation(item: ProviderObservation) -> Observation:
    return Observation(
        id=item.id,
        parent_id=item.parent_id,
        type=item.type,
        name=item.name,
        started_at=item.started_at,
        ended_at=item.ended_at,
        duration_ms=item.duration_ms,
        status=item.status,
        model=item.model,
        usage=item.usage,
        cost_usd=item.cost_usd,
        input=item.input,
        output=item.output,
        metadata=item.metadata,
    )


def _validate_provider_page(page: ProviderTracePage, *, limit: int) -> ProviderTracePage:
    if not isinstance(page, ProviderTracePage) or not isinstance(page.items, tuple):
        raise TraceQueryProviderError("malformed")
    if len(page.items) > limit:
        raise TraceQueryProviderError("response_too_large")
    for item in page.items:
        _validate_provider_summary(item)
    if page.next_cursor is not None:
        _provider_text(page.next_cursor, max_bytes=_MAX_PROVIDER_CURSOR_BYTES)
    return page


def _validate_provider_detail(
    detail: ProviderTraceDetail | None,
    *,
    trace_id: str,
) -> ProviderTraceDetail | None:
    if detail is None:
        return None
    if not isinstance(detail, ProviderTraceDetail) or not isinstance(detail.observations, tuple):
        raise TraceQueryProviderError("malformed")
    _validate_provider_summary(detail.trace)
    if detail.trace.id != trace_id:
        raise TraceQueryProviderError("malformed")
    if len(detail.observations) > _MAX_PROVIDER_OBSERVATIONS:
        raise TraceQueryProviderError("response_too_large")
    observation_ids: set[str] = set()
    for observation in detail.observations:
        _validate_provider_observation(observation)
        if observation.id in observation_ids:
            raise TraceQueryProviderError("malformed")
        observation_ids.add(observation.id)
    return detail


def _validate_provider_summary(item: ProviderTraceSummary) -> None:
    if not isinstance(item, ProviderTraceSummary):
        raise TraceQueryProviderError("malformed")
    _provider_text(item.id, max_bytes=512)
    _provider_text(item.name)
    _provider_interval(item.started_at, item.ended_at, item.duration_ms)
    _provider_status(item.trace_status)
    correlation = item.correlation
    if not isinstance(correlation, TraceCorrelation):
        raise TraceQueryProviderError("malformed")
    for value in (
        correlation.organization_id,
        correlation.workspace_id,
        correlation.session_id,
        correlation.thread_id,
        correlation.run_id,
        correlation.run_attempt_id,
        correlation.agent_id,
    ):
        _provider_text(value, max_bytes=_MAX_PROVIDER_ID_BYTES)
    _provider_json(item.input, max_bytes=_MAX_PROVIDER_CONTENT_BYTES)
    _provider_json(item.output, max_bytes=_MAX_PROVIDER_CONTENT_BYTES)
    if item.observation_count is not None and (
        not isinstance(item.observation_count, int)
        or isinstance(item.observation_count, bool)
        or item.observation_count < 0
    ):
        raise TraceQueryProviderError("malformed")
    if not isinstance(item.models, tuple) or len(item.models) > _MAX_PROVIDER_MODELS:
        raise TraceQueryProviderError("response_too_large")
    for model in item.models:
        _provider_text(model)
    _provider_usage(item.usage)
    _provider_cost(item.total_cost_usd)
    if item.source_url is not None:
        _provider_source_url(item.source_url)


def _validate_provider_observation(item: ProviderObservation) -> None:
    if not isinstance(item, ProviderObservation):
        raise TraceQueryProviderError("malformed")
    _provider_text(item.id, max_bytes=512)
    if item.parent_id is not None:
        _provider_text(item.parent_id, max_bytes=512)
    if item.type not in {"span", "generation", "event", "unknown"}:
        raise TraceQueryProviderError("malformed")
    _provider_text(item.name)
    _provider_interval(item.started_at, item.ended_at, item.duration_ms)
    _provider_status(item.status)
    if item.model is not None:
        _provider_text(item.model)
    _provider_usage(item.usage)
    _provider_cost(item.cost_usd)
    _provider_json(item.input, max_bytes=_MAX_PROVIDER_CONTENT_BYTES)
    _provider_json(item.output, max_bytes=_MAX_PROVIDER_CONTENT_BYTES)
    if not isinstance(item.metadata, Mapping):
        raise TraceQueryProviderError("malformed")
    if len(item.metadata) > _MAX_PROVIDER_METADATA_ENTRIES:
        raise TraceQueryProviderError("response_too_large")
    _provider_json(item.metadata, max_bytes=_MAX_PROVIDER_METADATA_BYTES)


def _provider_text(value: object, *, max_bytes: int = _MAX_PROVIDER_TEXT_BYTES) -> None:
    if not isinstance(value, str) or not _valid_bounded_text(value, max_bytes=max_bytes):
        raise TraceQueryProviderError("malformed")


def _provider_interval(started_at: object, ended_at: object, duration_ms: object) -> None:
    if not isinstance(started_at, datetime) or started_at.tzinfo is None:
        raise TraceQueryProviderError("malformed")
    if ended_at is not None and (
        not isinstance(ended_at, datetime) or ended_at.tzinfo is None or ended_at < started_at
    ):
        raise TraceQueryProviderError("malformed")
    if duration_ms is not None and (
        not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms < 0
    ):
        raise TraceQueryProviderError("malformed")


def _provider_status(value: object) -> None:
    if value not in {"unset", "ok", "error"}:
        raise TraceQueryProviderError("malformed")


def _provider_json(value: object, *, max_bytes: int) -> None:
    if value is None:
        return
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as error:
        raise TraceQueryProviderError("malformed") from error
    if len(encoded) > max_bytes:
        raise TraceQueryProviderError("response_too_large")


def _provider_usage(value: Mapping[str, int] | None) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise TraceQueryProviderError("malformed")
    if len(value) > _MAX_PROVIDER_USAGE_ENTRIES:
        raise TraceQueryProviderError("response_too_large")
    for key, amount in value.items():
        _provider_text(key, max_bytes=128)
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            raise TraceQueryProviderError("malformed")


def _provider_cost(value: Decimal | None) -> None:
    if value is not None and (not isinstance(value, Decimal) or not value.is_finite() or value < 0):
        raise TraceQueryProviderError("malformed")


def _provider_source_url(value: str) -> None:
    _provider_text(value, max_bytes=2048)
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise TraceQueryProviderError("malformed")


def _provider_error(error: TraceQueryProviderError) -> TraceQueryError:
    if error.failure == "version_unsupported":
        return TraceQueryError(
            "trace_query_provider_version_unsupported",
            "The selected Trace Query provider version is unsupported.",
            status_code=503,
        )
    if error.failure == "filter_unsupported":
        return TraceQueryError(
            "trace_query_filter_unsupported",
            "The selected Trace Query provider does not support this filter.",
            status_code=400,
        )
    return TraceQueryError(
        "trace_query_unavailable",
        "Trace Query is temporarily unavailable.",
        status_code=503,
    )


def _not_found() -> TraceQueryError:
    return TraceQueryError("trace_not_found", "The Trace was not found.", status_code=404)


def _cursor_scope(
    *,
    actor: AuthenticatedActor,
    provider_key: str,
    scope: TraceQueryScope,
    limit: int,
    query: str | None,
    search_in: SearchIn | None,
    thread_id: str | None,
    run_id: str | None,
    run_attempt_id: str | None,
) -> dict[str, object]:
    return {
        "principal_type": actor.principal.principal_type.value,
        "principal_id": actor.principal.principal_id,
        "boundary_workspace_id": actor.boundary_workspace_id,
        "provider": provider_key,
        "organization_id": scope.organization_id,
        "workspace_id": scope.workspace_id,
        "limit": limit,
        "query": query,
        "search_in": search_in.value if search_in is not None else None,
        "thread_id": thread_id,
        "run_id": run_id,
        "run_attempt_id": run_attempt_id,
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise TraceQueryError(
            "invalid_request",
            "Trace Query timestamps must include a UTC offset.",
            status_code=400,
            details={"fields": ["from", "to"]},
        )
    return value.astimezone(UTC)


__all__ = [
    "AuthorizedRunAttempt",
    "TraceAccessAuthorizer",
    "TraceQueryScope",
    "TraceQueryService",
]
