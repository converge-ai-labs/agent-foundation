"""Authorized, bounded Trace Query orchestration; no telemetry aggregation."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from urllib.parse import urlsplit

import anyio

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam import AuthenticatedActor
from a13n_service.temporal import Clock, utc_now

from .cursors import TraceCursorError, decode_trace_cursor, encode_trace_cursor
from .domain import (
    Observation,
    ObservationCollection,
    ProviderTraceQuery,
    ProviderTraceRead,
    SearchIn,
    Trace,
    TraceCollection,
    TraceCorrelation,
    TraceQueryDescriptor,
    TraceView,
    project_observation,
    project_trace,
)
from .errors import TraceQueryError, TraceQueryProviderError
from .provider import TraceQueryProvider

_MAX_RANGE = timedelta(days=31)
_DEFAULT_RANGE = timedelta(hours=24)
_HISTORY_ORIGIN = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class TraceQueryScope:
    organization_id: str
    workspace_id: str


@dataclass(frozen=True, slots=True)
class AuthorizedRunAttempt:
    run_attempt_id: str


class TraceAccessAuthorizer(Protocol):
    """Run-domain authorization port; implementations own their short sessions."""

    async def resolve_scope(self, *, actor: AuthenticatedActor, workspace_id: str) -> TraceQueryScope: ...

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
        clock: Clock | None = None,
    ) -> None:
        self._provider_key = provider_key
        self._provider = provider
        self._authorizer = authorizer
        self._clock = clock or utc_now

    async def describe(self, *, actor: AuthenticatedActor, workspace_id: str) -> TraceQueryDescriptor:
        authorizer = self._require_authorizer()
        await authorizer.resolve_scope(actor=actor, workspace_id=workspace_id)
        capabilities = self._provider.capabilities if self._provider is not None else None
        return TraceQueryDescriptor(
            provider=self._provider_key,
            enabled=self._provider is not None,
            search_in=capabilities.search_in if capabilities else (),
            history_from=capabilities.history_from if capabilities else None,
        )

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        from_started_at: datetime | None = None,
        to_started_at: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
        query: str | None = None,
        search_in: SearchIn | None = None,
        session_id: str | None = None,
        thread_id: str | None = None,
        run_id: str | None = None,
        run_attempt_id: str | None = None,
        metadata: Sequence[str] = (),
        view: TraceView = TraceView.compact,
    ) -> TraceCollection:
        provider, authorizer = self._require_available()
        _validate_limit(limit)
        if (query is None) != (search_in is None) or (query is not None and not _valid_text(query, 512)):
            raise _invalid("query and search_in must be supplied together with a bounded search value.")
        for value in (session_id, thread_id, run_id, run_attempt_id):
            if value is not None and not _valid_text(value, 1024):
                raise _invalid("A Trace Query correlation filter is invalid.")
        metadata_pairs = _metadata_pairs(metadata)
        if query is not None and search_in not in provider.capabilities.search_in:
            raise _provider_error(TraceQueryProviderError("filter_unsupported"))
        scope = await authorizer.resolve_scope(actor=actor, workspace_id=workspace_id)
        binding = self._binding(
            actor,
            scope,
            "traces",
            view,
            limit,
            {
                "query": query,
                "search_in": search_in,
                "session_id": session_id,
                "thread_id": thread_id,
                "run_id": run_id,
                "run_attempt_id": run_attempt_id,
                "metadata": [f"{key}={value}" for key, value in metadata_pairs],
            },
        )
        decoded = _decode(cursor, binding)
        if decoded is not None:
            start, end = decoded.from_started_at, decoded.to_started_at
            if from_started_at is not None or to_started_at is not None:
                if _range(from_started_at, to_started_at, self._clock()) != (
                    start,
                    end,
                ):
                    raise _invalid_cursor()
            # Revalidate even decoded ranges: cursors are not authority.
            _range(start, end, self._clock())
        else:
            start, end = _range(from_started_at, to_started_at, self._clock())
        if provider.capabilities.history_from is not None and start < provider.capabilities.history_from:
            raise _provider_error(TraceQueryProviderError("filter_unsupported"))
        provider_query = ProviderTraceQuery(
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            from_started_at=start,
            to_started_at=end,
            limit=limit,
            view=view,
            cursor=decoded.provider_cursor if decoded else None,
            query=query,
            search_in=search_in,
            session_id=session_id,
            thread_id=thread_id,
            run_id=run_id,
            run_attempt_id=run_attempt_id,
            metadata=metadata_pairs,
        )
        page = await _read(provider.list_traces(provider_query))
        try:
            _validate_page(page, limit, provider_query.cursor)
            for item in page.items:
                _validate_trace(item, self._provider_key)
            candidates = tuple(
                item
                for item in page.items
                if _matches(item, scope)
                and (session_id is None or item.correlation.session_id == session_id)
                and (thread_id is None or item.correlation.thread_id == thread_id)
                and (run_id is None or item.correlation.run_id == run_id)
                and (run_attempt_id is None or item.correlation.run_attempt_id == run_attempt_id)
            )
            if len({item.correlation.run_attempt_id for item in candidates}) != len(candidates):
                raise TraceQueryProviderError("malformed")
        except TraceQueryProviderError as error:
            raise _provider_error(error) from error
        authorized = await authorizer.authorize_run_attempts(
            actor=actor,
            scope=scope,
            correlations=tuple(item.correlation for item in candidates),
        )
        # Backend-native tie order must survive authorization filtering.
        return TraceCollection(
            items=tuple(project_trace(item, view) for item in candidates if _authorized(item, authorized)),
            next_cursor=_encode(page.next_cursor, binding, start, end),
        )

    async def get(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        trace_id: str,
        view: TraceView = TraceView.full,
    ) -> Trace:
        provider, authorizer = self._require_available()
        _validate_trace_id(trace_id)
        scope = await authorizer.resolve_scope(actor=actor, workspace_id=workspace_id)
        query = self._exact_query(scope, trace_id, view)
        trace = await self._root(provider, query, scope)
        await _authorize_exact(authorizer, actor, scope, trace)
        return project_trace(trace, view)

    async def list_observations(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        trace_id: str,
        view: TraceView = TraceView.compact,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ObservationCollection:
        provider, authorizer = self._require_available()
        _validate_trace_id(trace_id)
        _validate_limit(limit)
        scope = await authorizer.resolve_scope(actor=actor, workspace_id=workspace_id)
        binding = self._binding(actor, scope, "observations", view, limit, {"trace_id": trace_id})
        decoded = _decode(cursor, binding)
        query = self._exact_query(scope, trace_id, view)
        if decoded is not None:
            if decoded.from_started_at != (query.history_from or _HISTORY_ORIGIN):
                raise _invalid_cursor()
            if decoded.to_started_at > query.to_started_at or decoded.to_started_at <= decoded.from_started_at:
                raise _invalid_cursor()
            query = replace(query, to_started_at=decoded.to_started_at)
        trace = await self._root(provider, replace(query, view=TraceView.compact), scope)
        await _authorize_exact(authorizer, actor, scope, trace)
        page = await _read(
            provider.list_observations(
                replace(
                    query,
                    limit=limit,
                    cursor=decoded.provider_cursor if decoded else None,
                )
            )
        )
        try:
            _validate_page(page, limit, decoded.provider_cursor if decoded else None)
            for item in page.items:
                _validate_observation(item)
        except TraceQueryProviderError as error:
            raise _provider_error(error) from error
        # Read authority afresh after child I/O, including credential eligibility.
        await _authorize_exact(authorizer, actor, scope, trace)
        return ObservationCollection(
            items=tuple(project_observation(item, view) for item in page.items),
            next_cursor=_encode(
                page.next_cursor,
                binding,
                query.history_from or _HISTORY_ORIGIN,
                query.to_started_at,
            ),
        )

    async def _root(
        self,
        provider: TraceQueryProvider,
        query: ProviderTraceRead,
        scope: TraceQueryScope,
    ) -> Trace:
        trace = await _read(provider.get_trace(query))
        if trace is None:
            raise _not_found()
        try:
            _validate_trace(trace, self._provider_key)
            if trace.id != query.trace_id:
                raise TraceQueryProviderError("malformed")
        except TraceQueryProviderError as error:
            raise _provider_error(error) from error
        if not _matches(trace, scope):
            raise _not_found()
        return trace

    def _exact_query(self, scope: TraceQueryScope, trace_id: str, view: TraceView) -> ProviderTraceRead:
        provider, _ = self._require_available()
        return ProviderTraceRead(
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            trace_id=trace_id,
            history_from=provider.capabilities.history_from,
            to_started_at=_as_utc(self._clock()),
            view=view,
        )

    def _binding(
        self,
        actor: AuthenticatedActor,
        scope: TraceQueryScope,
        collection: str,
        view: TraceView,
        limit: int,
        filters: dict[str, object],
    ) -> dict[str, object]:
        provider, _ = self._require_available()
        return {
            "principal_type": actor.principal.principal_type.value,
            "principal_id": actor.principal.principal_id,
            "boundary_workspace_id": actor.boundary_workspace_id,
            "boundary_organization_id": actor.boundary_organization_id,
            "provider": self._provider_key,
            "namespace": provider.cursor_namespace,
            "organization_id": scope.organization_id,
            "workspace_id": scope.workspace_id,
            "collection": collection,
            "view": view.value,
            "limit": limit,
            "history_from": (
                provider.capabilities.history_from.isoformat() if provider.capabilities.history_from else None
            ),
            **filters,
        }

    def _require_authorizer(self) -> TraceAccessAuthorizer:
        if self._authorizer is None:
            raise _provider_error(TraceQueryProviderError("unavailable"))
        return self._authorizer

    def _require_available(self) -> tuple[TraceQueryProvider, TraceAccessAuthorizer]:
        authorizer = self._require_authorizer()
        if self._provider is None:
            raise _provider_error(TraceQueryProviderError("unavailable"))
        return self._provider, authorizer


async def _read[T](operation: Awaitable[T]) -> T:
    try:
        with anyio.fail_after(15):
            return await operation
    except TimeoutError as error:
        raise _provider_error(TraceQueryProviderError("unavailable")) from error
    except TraceQueryProviderError as error:
        raise _provider_error(error) from error


async def _authorize_exact(
    authorizer: TraceAccessAuthorizer,
    actor: AuthenticatedActor,
    scope: TraceQueryScope,
    trace: Trace,
) -> None:
    authorized = await authorizer.authorize_run_attempts(actor=actor, scope=scope, correlations=(trace.correlation,))
    if not _authorized(trace, authorized):
        raise _not_found()


def _authorized(trace: Trace, authorized: Mapping[str, AuthorizedRunAttempt]) -> bool:
    decision = authorized.get(trace.correlation.run_attempt_id)
    return decision is not None and decision.run_attempt_id == trace.correlation.run_attempt_id


def _matches(trace: Trace, scope: TraceQueryScope) -> bool:
    return (
        trace.correlation.organization_id == scope.organization_id
        and trace.correlation.workspace_id == scope.workspace_id
    )


def _decode(cursor: str | None, binding: dict[str, object]):
    try:
        return decode_trace_cursor(cursor, scope=binding) if cursor is not None else None
    except TraceCursorError as error:
        raise _invalid_cursor() from error


def _encode(cursor: str | None, binding: dict[str, object], start: datetime, end: datetime) -> str | None:
    try:
        return (
            encode_trace_cursor(
                provider_cursor=cursor,
                scope=binding,
                from_started_at=start,
                to_started_at=end,
            )
            if cursor is not None
            else None
        )
    except TraceCursorError as error:
        raise _provider_error(TraceQueryProviderError("malformed")) from error


def _metadata_pairs(entries: Sequence[str]) -> tuple[tuple[str, str], ...]:
    if len(entries) > 8:
        raise _invalid("At most eight Trace Query metadata filters are allowed.")
    pairs: dict[str, str] = {}
    for entry in entries:
        key, separator, value = entry.partition("=")
        key = key.strip()
        if not separator or not _valid_text(key, 256) or not _valid_text(value, 1024):
            raise _invalid("A Trace Query metadata filter is invalid.")
        if key in pairs:
            raise _invalid("Trace Query metadata keys must be unique.")
        pairs[key] = value
    return tuple(sorted(pairs.items()))


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not 1 <= limit <= 100:
        raise _invalid("The collection limit must be between 1 and 100.")


def _validate_trace_id(value: str) -> None:
    if not _valid_text(value, 512):
        raise _invalid("The Trace identifier is invalid.")


def _range(start: datetime | None, end: datetime | None, now: datetime) -> tuple[datetime, datetime]:
    if (start is None) != (end is None):
        raise _invalid("Trace Query requires both from and to.")
    if start is None or end is None:
        normalized_end = _as_utc(now)
        return normalized_end - _DEFAULT_RANGE, normalized_end
    start, end = _as_utc(start), _as_utc(end)
    if start >= end or end - start > _MAX_RANGE:
        raise _invalid("The Trace Query time range is invalid.")
    return start, end


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise _invalid("Trace Query timestamps must include a UTC offset.")
    return value.astimezone(UTC)


def _valid_text(value: str, max_bytes: int) -> bool:
    try:
        return bool(value) and "\x00" not in value and len(value.encode("utf-8")) <= max_bytes
    except UnicodeEncodeError:
        return False


def _text(value: object, max_bytes: int = 4096) -> None:
    if not isinstance(value, str) or not _valid_text(value, max_bytes):
        raise TraceQueryProviderError("malformed")


def _json(value: object, max_bytes: int) -> None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as error:
        raise TraceQueryProviderError("malformed") from error
    if len(encoded) > max_bytes:
        raise TraceQueryProviderError("response_too_large")


def _validate_page(page: TraceCollection | ObservationCollection, limit: int, previous: str | None) -> None:
    if not isinstance(page, (TraceCollection, ObservationCollection)):
        raise TraceQueryProviderError("malformed")
    if len(page.items) > limit:
        raise TraceQueryProviderError("response_too_large")
    if len({item.id for item in page.items}) != len(page.items):
        raise TraceQueryProviderError("malformed")
    if page.next_cursor is not None:
        _text(page.next_cursor, 4096)
        if page.next_cursor == previous:
            raise TraceQueryProviderError("malformed")
    starts = [item.root.started_at if isinstance(item, Trace) else item.started_at for item in page.items]
    if starts != sorted(starts, reverse=True):
        raise TraceQueryProviderError("malformed")
    _json(page.model_dump(mode="json"), 8 * 1024 * 1024)


def _validate_trace(item: Trace, provider: str) -> None:
    if not isinstance(item, Trace) or item.provider != provider:
        raise TraceQueryProviderError("malformed")
    _text(item.id, 512)
    for value in item.correlation.model_dump().values():
        _text(value, 1024)
    _validate_observation(item.root)
    if item.root.name != "a13n.service.run_attempt" or item.root.parent_id is not None:
        raise TraceQueryProviderError("malformed")
    if item.source_url is not None:
        _text(item.source_url, 2048)
        parsed = urlsplit(item.source_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise TraceQueryProviderError("malformed")


def _validate_observation(item: Observation) -> None:
    if not isinstance(item, Observation):
        raise TraceQueryProviderError("malformed")
    _text(item.id, 512)
    if item.parent_id is not None:
        _text(item.parent_id, 512)
    _text(item.type, 128)
    _text(item.name)
    if item.started_at.tzinfo is None or (
        item.ended_at is not None and (item.ended_at.tzinfo is None or item.ended_at < item.started_at)
    ):
        raise TraceQueryProviderError("malformed")
    if item.status not in {None, "unset", "ok", "error"}:
        raise TraceQueryProviderError("malformed")
    for text in (item.level, item.status_message):
        if text is not None:
            _text(text)
    if item.model is not None:
        for text in (item.model.requested, item.model.response):
            if text is not None:
                _text(text)
    if item.usage is not None:
        if len(item.usage) > 64:
            raise TraceQueryProviderError("response_too_large")
        for key, amount in item.usage.items():
            _text(key, 128)
            if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
                raise TraceQueryProviderError("malformed")
    if item.cost_usd is not None and (
        not isinstance(item.cost_usd, Decimal) or not item.cost_usd.is_finite() or item.cost_usd < 0
    ):
        raise TraceQueryProviderError("malformed")
    for content in (item.input, item.output):
        if content is not None:
            _json(content.model_dump(), 1024 * 1024)
    for attrs in (item.attributes, item.resource_attributes):
        if attrs is not None and len(attrs) > 512:
            raise TraceQueryProviderError("response_too_large")
        _json(attrs, 256 * 1024)
    _json(item.model_dump(mode="json", exclude={"input", "output"}), 1024 * 1024)


def _invalid(message: str) -> TraceQueryError:
    return TraceQueryError("invalid_request", message, category=ErrorCategory.invalid_request)


def _invalid_cursor() -> TraceQueryError:
    return TraceQueryError(
        "invalid_cursor",
        "The collection cursor is invalid.",
        category=ErrorCategory.invalid_request,
    )


def _provider_error(error: TraceQueryProviderError) -> TraceQueryError:
    if error.failure == "invalid_cursor":
        return _invalid_cursor()
    if error.failure == "version_unsupported":
        return TraceQueryError(
            "trace_query_provider_version_unsupported",
            "The selected Trace Query provider version is unsupported.",
            category=ErrorCategory.unavailable,
        )
    if error.failure == "filter_unsupported":
        return TraceQueryError(
            "trace_query_filter_unsupported",
            "The selected Trace Query provider does not support this filter.",
            category=ErrorCategory.invalid_request,
        )
    return TraceQueryError(
        "trace_query_unavailable",
        "Trace Query is temporarily unavailable.",
        category=ErrorCategory.unavailable,
    )


def _not_found() -> TraceQueryError:
    return TraceQueryError("trace_not_found", "The Trace was not found.", category=ErrorCategory.not_found)
