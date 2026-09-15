from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from a13n_service.http_errors import application_error_status
from a13n_service.iam import AuthenticatedActor, PrincipalRef, PrincipalType
from a13n_service.trace_query import (
    AuthorizedRunAttempt,
    Content,
    Observation,
    ObservationCollection,
    ProviderTraceQuery,
    ProviderTraceRead,
    SearchIn,
    Trace,
    TraceCollection,
    TraceCorrelation,
    TraceQueryCapabilities,
    TraceQueryError,
    TraceQueryProviderError,
    TraceQueryScope,
    TraceQueryService,
    TraceView,
)


def actor(*, principal_id: str = "user_0000000000000001") -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type=PrincipalType.user, principal_id=principal_id),
        auth_method="test",
        credential_id="credential-test",
        boundary_workspace_id="ws-1",
    )


def summary(**updates: object) -> Trace:
    correlation = TraceCorrelation(
        organization_id="org-1",
        workspace_id="ws-1",
        session_id="session-1",
        thread_id="thread-1",
        run_id="run-1",
        run_attempt_id="attempt-1",
        agent_id="agent-1",
    )
    root = Observation(
        id="root-1",
        parent_id=None,
        type="span",
        name="a13n.service.run_attempt",
        started_at=datetime(2026, 9, 1, 1, tzinfo=UTC),
        ended_at=datetime(2026, 9, 1, 1, 0, 1, tzinfo=UTC),
        status=None,
        level="default",
        status_message="root message",
        model=None,
        usage={"total": 12},
        cost_usd=None,
        input=Content(media_type="text/plain", value="hello"),
        output=Content(media_type="text/plain", value="world"),
        attributes={"phase": "root"},
        resource_attributes={"service.name": "service"},
        scope=None,
        events=None,
        links=None,
    )
    if "input" in updates:
        root = root.model_copy(update={"input": Content(media_type=None, value=updates.pop("input"))})
    values = {
        "id": "trace-1",
        "provider": "fixture",
        "correlation": correlation,
        "root": root,
        "source_url": "https://langfuse.example.com/trace-1",
        **updates,
    }
    return Trace.model_validate(values)


class Provider:
    capabilities = TraceQueryCapabilities(search_in=(SearchIn.input, SearchIn.output))
    cursor_namespace = "fixture-project"

    def __init__(self, *, items: tuple[Trace, ...] = (summary(),)) -> None:
        self.items = items
        self.queries: list[ProviderTraceQuery] = []
        self.reads: list[ProviderTraceRead] = []
        self.detail: Trace | None = items[0] if items else None
        self.observations = ObservationCollection(items=tuple(item.root for item in items[:1]), next_cursor=None)
        self.failure: TraceQueryProviderError | None = None

    async def list_traces(self, query: ProviderTraceQuery) -> TraceCollection:
        self.queries.append(query)
        if self.failure is not None:
            raise self.failure
        return TraceCollection(
            items=self.items,
            next_cursor="provider-next" if query.cursor is None else None,
        )

    async def get_trace(self, query: ProviderTraceRead) -> Trace | None:
        self.reads.append(query)
        if self.failure is not None:
            raise self.failure
        return self.detail

    async def list_observations(self, query: ProviderTraceRead) -> ObservationCollection:
        self.reads.append(query)
        if self.failure is not None:
            raise self.failure
        return self.observations


class Authorizer:
    def __init__(self, *, visible: frozenset[str] = frozenset({"attempt-1"})) -> None:
        self.visible = visible

    async def resolve_scope(self, *, actor: AuthenticatedActor, workspace_id: str) -> TraceQueryScope:
        del actor
        return TraceQueryScope(organization_id="org-1", workspace_id=workspace_id)

    async def authorize_run_attempts(
        self,
        *,
        actor: AuthenticatedActor,
        scope: TraceQueryScope,
        correlations: tuple[TraceCorrelation, ...],
    ) -> dict[str, AuthorizedRunAttempt]:
        del actor, scope
        return {
            item.run_attempt_id: AuthorizedRunAttempt(
                run_attempt_id=item.run_attempt_id,
            )
            for item in correlations
            if item.run_attempt_id in self.visible
        }


def service(
    provider: Provider | None = None,
    authorizer: Authorizer | None = None,
) -> TraceQueryService:
    return TraceQueryService(
        provider_key="fixture",
        provider=provider,
        authorizer=authorizer,
        clock=lambda: datetime(2026, 9, 2, tzinfo=UTC),
    )


async def list_traces(
    service: TraceQueryService,
    *,
    cursor: str | None = None,
    query: str | None = None,
    search_in: SearchIn | None = None,
) -> object:
    return await service.list(
        actor=actor(),
        workspace_id="ws-1",
        from_started_at=None,
        to_started_at=None,
        limit=50,
        cursor=cursor,
        query=query,
        search_in=search_in,
        thread_id=None,
        run_id=None,
        run_attempt_id=None,
    )


@pytest.mark.anyio
async def test_metadata_filters_normalize_and_reach_the_provider() -> None:
    adapter = Provider()
    await service(adapter, Authorizer()).list(
        actor=actor(),
        workspace_id="ws-1",
        metadata=(" synthetic = true ", "scenario=review"),
    )
    assert adapter.queries[-1].metadata == (
        ("scenario", "review"),
        ("synthetic", " true "),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "entries",
    (
        ["no-equals"],
        ["=value"],
        ["key="],
        ["dup=1", "dup=2"],
        [f"key-{index}=v" for index in range(9)],
        ["ke\x00y=v"],
        ["k" * 257 + "=v"],
        ["key=" + "v" * 1025],
    ),
)
async def test_metadata_filters_reject_malformed_entries(entries: list[str]) -> None:
    adapter = Provider()
    with pytest.raises(TraceQueryError) as raised:
        await service(adapter, Authorizer()).list(actor=actor(), workspace_id="ws-1", metadata=entries)
    assert raised.value.code == "invalid_request"
    assert not adapter.queries


@pytest.mark.anyio
async def test_organization_session_can_page_both_collections_without_workspace_header() -> None:
    browser = replace(
        actor(),
        auth_method="session",
        boundary_workspace_id=None,
        boundary_organization_id="org-1",
    )
    adapter = Provider()
    queries = service(adapter, Authorizer())
    first = await queries.list(actor=browser, workspace_id="ws-1")
    assert first.items and first.next_cursor
    second = await queries.list(actor=browser, workspace_id="ws-1", cursor=first.next_cursor)
    assert second.next_cursor is None
    with pytest.raises(TraceQueryError, match="cursor"):
        await queries.list(actor=actor(), workspace_id="ws-1", cursor=first.next_cursor)
    adapter.observations = ObservationCollection(items=(summary().root,), next_cursor="children-next")
    children = await queries.list_observations(actor=browser, workspace_id="ws-1", trace_id="trace-1")
    assert children.items and children.next_cursor
    adapter.observations = ObservationCollection(items=(), next_cursor=None)
    last = await queries.list_observations(
        actor=browser,
        workspace_id="ws-1",
        trace_id="trace-1",
        cursor=children.next_cursor,
    )
    assert last.items == () and last.next_cursor is None
    with pytest.raises(TraceQueryError, match="cursor"):
        await queries.list_observations(
            actor=actor(),
            workspace_id="ws-1",
            trace_id="trace-1",
            cursor=children.next_cursor,
        )


@pytest.mark.anyio
async def test_list_forces_scope_authorizes_results_and_projects_root_consistently() -> None:
    provider = Provider()

    result = await list_traces(service(provider, Authorizer()))

    assert result.items[0].root.status is None
    assert result.items[0].root.input is None
    assert result.items[0].root.attributes is None
    assert "run_attempt_outcome" not in result.items[0].model_dump()
    assert result.next_cursor is not None  # type: ignore[attr-defined]
    query = provider.queries[0]
    assert query.organization_id == "org-1"
    assert query.workspace_id == "ws-1"
    assert query.from_started_at == datetime(2026, 9, 1, tzinfo=UTC)
    assert query.to_started_at == datetime(2026, 9, 2, tzinfo=UTC)


@pytest.mark.anyio
async def test_list_omits_unauthorized_and_cross_scope_provider_results() -> None:
    cross_scope = summary(
        id="trace-cross",
        correlation=summary().correlation.model_copy(update={"workspace_id": "ws-other"}),
    )
    provider = Provider(items=(summary(), cross_scope))

    result = await list_traces(service(provider, Authorizer(visible=frozenset())))

    assert result.items == ()  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_cursor_is_bound_to_actor_and_complete_query() -> None:
    provider = Provider()
    trace_service = service(provider, Authorizer())
    first = await list_traces(trace_service)

    await trace_service.list(
        actor=actor(),
        workspace_id="ws-1",
        from_started_at=datetime(2026, 9, 1, tzinfo=UTC),
        to_started_at=datetime(2026, 9, 2, tzinfo=UTC),
        limit=50,
        cursor=first.next_cursor,  # type: ignore[attr-defined]
        query=None,
        search_in=None,
        thread_id=None,
        run_id=None,
        run_attempt_id=None,
    )
    assert provider.queries[-1].cursor == "provider-next"

    with pytest.raises(TraceQueryError) as raised:
        await trace_service.list(
            actor=actor(principal_id="user_0000000000000002"),
            workspace_id="ws-1",
            from_started_at=datetime(2026, 9, 1, tzinfo=UTC),
            to_started_at=datetime(2026, 9, 2, tzinfo=UTC),
            limit=50,
            cursor=first.next_cursor,  # type: ignore[attr-defined]
            query=None,
            search_in=None,
            thread_id=None,
            run_id=None,
            run_attempt_id=None,
        )
    assert raised.value.code == "invalid_cursor"


@pytest.mark.anyio
async def test_cursor_preserves_the_default_time_window_across_pages() -> None:
    provider = Provider()
    times = iter(
        (
            datetime(2026, 9, 2, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 2, 1, 0, tzinfo=UTC),
        )
    )
    trace_service = TraceQueryService(
        provider_key="fixture",
        provider=provider,
        authorizer=Authorizer(),
        clock=lambda: next(times),
    )

    first = await list_traces(trace_service)
    second = await list_traces(trace_service, cursor=first.next_cursor)  # type: ignore[arg-type,attr-defined]

    assert second.items  # type: ignore[attr-defined]
    assert provider.queries[1].from_started_at == provider.queries[0].from_started_at
    assert provider.queries[1].to_started_at == provider.queries[0].to_started_at


@pytest.mark.anyio
async def test_exact_read_conceals_absent_cross_scope_and_unauthorized_traces() -> None:
    provider = Provider()
    provider.detail = None
    with pytest.raises(TraceQueryError) as absent:
        await service(provider, Authorizer()).get(
            actor=actor(), workspace_id="ws-1", trace_id="trace-1", view=TraceView.full
        )
    assert absent.value.code == "trace_not_found"

    provider.detail = summary()
    with pytest.raises(TraceQueryError) as concealed:
        await service(provider, Authorizer(visible=frozenset())).get(
            actor=actor(), workspace_id="ws-1", trace_id="trace-1", view=TraceView.full
        )
    assert concealed.value.code == "trace_not_found"


@pytest.mark.anyio
async def test_unavailable_provider_and_unsupported_search_fail_safely() -> None:
    with pytest.raises(TraceQueryError) as unavailable:
        await list_traces(service(None, Authorizer()))
    assert application_error_status(unavailable.value) == 503

    with pytest.raises(TraceQueryError) as unsupported:
        await list_traces(
            service(Provider(), Authorizer()),
            query="hello",
            search_in=SearchIn.input_output,
        )
    assert unsupported.value.code == "trace_query_filter_unsupported"


@pytest.mark.anyio
async def test_provider_failures_do_not_disclose_backend_details() -> None:
    provider = Provider()
    provider.failure = TraceQueryProviderError("malformed")

    with pytest.raises(TraceQueryError) as raised:
        await list_traces(service(provider, Authorizer()))

    assert raised.value.code == "trace_query_unavailable"
    assert raised.value.message == "Trace Query is temporarily unavailable."


@pytest.mark.anyio
async def test_invalid_exact_filter_and_trace_id_fail_before_provider_io() -> None:
    provider = Provider()
    trace_service = service(provider, Authorizer())

    with pytest.raises(TraceQueryError) as invalid_filter:
        await trace_service.list(
            actor=actor(),
            workspace_id="ws-1",
            from_started_at=None,
            to_started_at=None,
            limit=50,
            cursor=None,
            query=None,
            search_in=None,
            thread_id="\x00",
            run_id=None,
            run_attempt_id=None,
        )
    with pytest.raises(TraceQueryError) as invalid_trace:
        await trace_service.get(
            actor=actor(),
            workspace_id="ws-1",
            trace_id="界" * 200,
            view=TraceView.full,
        )

    assert invalid_filter.value.code == "invalid_request"
    assert invalid_trace.value.code == "invalid_request"
    assert provider.queries == []


@pytest.mark.anyio
async def test_duplicate_trace_or_attempt_correlation_fails_safely() -> None:
    duplicate = summary(id="trace-2")
    provider = Provider(items=(summary(), duplicate))

    with pytest.raises(TraceQueryError) as raised:
        await list_traces(service(provider, Authorizer()))

    assert raised.value.code == "trace_query_unavailable"


@pytest.mark.anyio
async def test_provider_page_is_rebounded_at_the_service_boundary() -> None:
    second = summary(
        id="trace-2",
        correlation=summary().correlation.model_copy(update={"run_attempt_id": "attempt-2"}),
    )
    provider = Provider(items=(summary(), second))

    with pytest.raises(TraceQueryError) as raised:
        await service(provider, Authorizer()).list(
            actor=actor(),
            workspace_id="ws-1",
            from_started_at=None,
            to_started_at=None,
            limit=1,
            cursor=None,
            query=None,
            search_in=None,
            thread_id=None,
            run_id=None,
            run_attempt_id=None,
        )

    assert raised.value.code == "trace_query_unavailable"


@pytest.mark.anyio
async def test_provider_unbounded_content_fails_safely() -> None:
    provider = Provider(items=(summary(input="x" * (1024 * 1024 + 1)),))

    with pytest.raises(TraceQueryError) as raised:
        await list_traces(service(provider, Authorizer()))

    assert raised.value.code == "trace_query_unavailable"


@pytest.mark.anyio
async def test_exact_provider_result_must_match_the_requested_trace_id() -> None:
    provider = Provider()
    provider.detail = summary(id="trace-other")

    with pytest.raises(TraceQueryError) as raised:
        await service(provider, Authorizer()).get(
            actor=actor(),
            workspace_id="ws-1",
            trace_id="trace-1",
            view=TraceView.full,
        )

    assert raised.value.code == "trace_query_unavailable"
