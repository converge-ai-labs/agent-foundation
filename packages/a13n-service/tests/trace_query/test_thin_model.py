"""Value fidelity and bounded reads, including sanitized real Gateway evidence."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import anyio
import httpx2
import pytest
from a13n_service.trace_query import (
    Content,
    ObservationCollection,
    ObservationEvent,
    ObservationLink,
    SearchIn,
    TraceQueryCapabilities,
    TraceQueryError,
    TraceQueryProviderError,
    TraceView,
)
from a13n_service.trace_query.domain import project_observation
from a13n_service.trace_query.langfuse import LangfuseTraceQueryProvider, _observation

from .test_langfuse import child, query, read_query, root
from .test_service import Authorizer, Provider, actor, service, summary


@pytest.mark.parametrize(
    "case", json.loads((Path(__file__).parent / "fixtures/langfuse_observations.json").read_text())
)
def test_real_backend_shapes_preserve_values(case):
    rows = case["rows"]
    observations = [_observation(row, view=TraceView.full) for row in rows]
    assert {item.id for item in observations} == {row["id"] for row in rows}
    for row, item in zip(rows, observations, strict=True):
        assert item.parent_id == row["parentObservationId"]
        assert item.type == row["type"].lower()
        assert item.usage == row["usageDetails"]
        assert item.status is None
        assert item.level == row["level"].lower()
        assert item.status_message == row["statusMessage"]
        assert item.events is item.links is None
        assert item.cost_usd is None
        attrs = row["metadata"]["attributes"]
        if item.model is not None:
            assert item.model.requested == attrs.get("gen_ai.request.model")
            assert item.model.response == attrs.get("gen_ai.response.model")
        for field in ("input", "output"):
            content = item.input if field == "input" else item.output
            expected = row[field]
            if expected is None:
                assert content is None
            else:
                assert content is not None
                if attrs.get(field + ".mime_type") == "application/json" and isinstance(expected, str):
                    expected = json.loads(expected)
                assert content.value == expected
        compact = project_observation(item, TraceView.compact)
        assert compact.input is compact.output is compact.status_message is compact.attributes is None
        assert compact.resource_attributes is compact.scope is compact.events is None
    if case["case"].endswith("tool_error_recovery"):
        usage = next(item.usage for item in observations if item.id == "b55c93fed1850211")
        assert usage == {**usage, "input": 3352, "output": 128, "reasoning_tokens": 26, "total": 3506}
        assert usage["total"] != usage["input"] + usage["output"]
    if case["case"].endswith("persist_failure"):
        assert next(item for item in observations if item.parent_id is None).output is None
        assert any(item.type == "generation" and item.output is not None for item in observations)


def test_fixture_coverage_is_not_silently_reduced():
    cases = json.loads((Path(__file__).parent / "fixtures/langfuse_observations.json").read_text())
    rows = [row for case in cases for row in case["rows"]]
    assert len(cases) == 6 and len(rows) == 72
    assert Counter(row["type"] for row in rows) == {"SPAN": 41, "GENERATION": 12, "AGENT": 16, "TOOL": 3}
    assert sum(bool(row["statusMessage"]) for row in rows) == 5


def test_unknown_types_and_absence_are_not_reinterpreted():
    row = child() | {
        "type": "NEW_OPERATION",
        "endTime": None,
        "level": "WARNING",
        "usageDetails": {"zero": 0},
        "statusMessage": "Diagnostic",
    }
    result = _observation(row, view=TraceView.full)
    assert (result.type, result.status, result.level, result.ended_at) == ("new_operation", None, "warning", None)
    assert result.usage == {"zero": 0} and result.status_message == "Diagnostic"
    assert _observation(child() | {"usageDetails": {}}, view=TraceView.full).usage == {}
    assert _observation(child() | {"usageDetails": None}, view=TraceView.full).usage is None


def test_content_keeps_json_null_distinct_from_absence_and_plain_text():
    row = root() | {"input": "null", "output": None}
    item = _observation(row, view=TraceView.full)
    assert item.input == Content(media_type="application/json", value=None)
    assert item.output is None
    row["metadata"]["attributes"].pop("input.mime_type")
    assert _observation(row, view=TraceView.full).input == Content(media_type=None, value="null")


def test_projection_preserves_event_duplicates_and_link_topology():
    item = summary().root
    event = ObservationEvent(name="exception", occurred_at=item.started_at, attributes={"exception.message": "fixture"})
    link = ObservationLink(trace_id="other-trace", observation_id="linked", attributes={"fixture": True})
    item = item.model_copy(update={"status": "unset", "events": (event, event), "links": (link,)})
    assert project_observation(item, TraceView.full).events == (event, event)
    compact = project_observation(item, TraceView.compact)
    assert compact.status == "unset" and compact.events is None
    assert compact.links[0].observation_id == "linked" and compact.links[0].attributes is None
    assert project_observation(item.model_copy(update={"links": ()}), TraceView.compact).links == ()


@pytest.mark.anyio
async def test_descriptor_requires_scope_and_only_advertises_supported_search():
    descriptor = await service(Provider(), Authorizer()).describe(actor=actor(), workspace_id="ws-1")
    assert descriptor.enabled and descriptor.search_in == (SearchIn.input, SearchIn.output)
    disabled = await service(None, Authorizer()).describe(actor=actor(), workspace_id="ws-1")
    assert not disabled.enabled and disabled.search_in == ()


@pytest.mark.anyio
async def test_tied_trace_order_is_native_not_locally_sorted():
    first = summary(id="trace-a")
    second = summary(id="trace-z", correlation=first.correlation.model_copy(update={"run_attempt_id": "attempt-2"}))
    provider = Provider(items=(first, second))
    result = await service(provider, Authorizer(visible=frozenset({"attempt-1", "attempt-2"}))).list(
        actor=actor(), workspace_id="ws-1"
    )
    assert [item.id for item in result.items] == ["trace-a", "trace-z"]


@pytest.mark.anyio
async def test_empty_authorization_page_keeps_continuation_without_extra_io():
    provider = Provider()
    result = await service(provider, Authorizer(visible=frozenset())).list(actor=actor(), workspace_id="ws-1")
    assert result.items == () and result.next_cursor is not None and len(provider.queries) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["view", "limit", "trace", "namespace", "actor"])
async def test_observation_cursor_binds_the_exact_read_context(change):
    provider = Provider()
    provider.observations = ObservationCollection(items=(), next_cursor="next")
    target = service(provider, Authorizer())
    page = await target.list_observations(actor=actor(), workspace_id="ws-1", trace_id="trace-1")
    args = {"actor": actor(), "workspace_id": "ws-1", "trace_id": "trace-1", "cursor": page.next_cursor}
    if change == "view":
        args["view"] = TraceView.full
    elif change == "limit":
        args["limit"] = 1
    elif change == "trace":
        args["trace_id"] = "trace-2"
    elif change == "namespace":
        provider.cursor_namespace = "new-project"
    else:
        args["actor"] = actor(principal_id="user_0000000000000002")
    with pytest.raises(TraceQueryError, match="cursor"):
        await target.list_observations(**args)


@pytest.mark.anyio
async def test_exact_history_is_not_the_recent_list_window():
    provider = Provider()
    target = service(provider, Authorizer())
    await target.get(actor=actor(), workspace_id="ws-1", trace_id="trace-1")
    assert provider.reads[0].history_from is None
    provider.capabilities = TraceQueryCapabilities(history_from=datetime(2020, 1, 1, tzinfo=UTC))
    await target.list_observations(actor=actor(), workspace_id="ws-1", trace_id="trace-1")
    assert all(read.history_from == datetime(2020, 1, 1, tzinfo=UTC) for read in provider.reads[1:])
    with pytest.raises(TraceQueryError) as caught:
        await target.list(
            actor=actor(),
            workspace_id="ws-1",
            from_started_at=datetime(2019, 1, 1, tzinfo=UTC),
            to_started_at=datetime(2019, 1, 2, tzinfo=UTC),
        )
    assert caught.value.code == "trace_query_filter_unsupported"


@pytest.mark.anyio
async def test_children_are_authorized_before_and_after_io():
    authorizer = Authorizer()

    class RevokingProvider(Provider):
        async def list_observations(self, query):
            authorizer.visible = frozenset()
            return await super().list_observations(query)

    provider = RevokingProvider()
    with pytest.raises(TraceQueryError) as caught:
        await service(provider, authorizer).list_observations(actor=actor(), workspace_id="ws-1", trace_id="trace-1")
    assert caught.value.code == "trace_not_found"
    provider.reads.clear()
    with pytest.raises(TraceQueryError):
        await service(provider, authorizer).list_observations(actor=actor(), workspace_id="ws-1", trace_id="trace-1")
    assert len(provider.reads) == 1  # root only; children are not read without authority


@pytest.mark.anyio
async def test_large_traces_use_bounded_pages_with_pinned_history():
    provider = Provider()
    original = summary().root
    count = 1101

    class PagedProvider(Provider):
        async def list_observations(self, query):
            self.reads.append(query)
            offset = int(query.cursor or "0")
            items = tuple(
                original.model_copy(update={"id": f"span-{i}", "parent_id": "root-1"})
                for i in range(offset, min(offset + query.limit, count))
            )
            return ObservationCollection(
                items=items, next_cursor=str(offset + len(items)) if offset + len(items) < count else None
            )

    provider = PagedProvider()
    target = service(provider, Authorizer())
    ids, cursor = [], None
    while True:
        page = await target.list_observations(
            actor=actor(), workspace_id="ws-1", trace_id="trace-1", cursor=cursor, limit=100
        )
        ids.extend(item.id for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert len(ids) == len(set(ids)) == count
    assert len({read.to_started_at for read in provider.reads}) == 1
    assert len(provider.reads) == 24


@pytest.mark.anyio
async def test_repeated_empty_cursor_fails_without_a_hidden_loop():
    provider = Provider()
    provider.observations = ObservationCollection(items=(), next_cursor="stuck")
    target = service(provider, Authorizer())
    first = await target.list_observations(actor=actor(), workspace_id="ws-1", trace_id="trace-1")
    with pytest.raises(TraceQueryError) as caught:
        await target.list_observations(actor=actor(), workspace_id="ws-1", trace_id="trace-1", cursor=first.next_cursor)
    assert caught.value.code == "trace_query_unavailable" and len(provider.reads) == 4


@pytest.mark.anyio
async def test_cancelled_provider_read_propagates():
    class BlockingProvider(Provider):
        async def list_traces(self, query):
            await anyio.sleep_forever()

    with anyio.move_on_after(0.01) as cancel_scope:
        await service(BlockingProvider(), Authorizer()).list(actor=actor(), workspace_id="ws-1")
    assert cancel_scope.cancelled_caught


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body,failure",
    [({"message": "Invalid cursor format"}, "invalid_cursor"), ({"message": "Invalid filter"}, "unavailable")],
)
async def test_backend_400_is_not_always_a_cursor_error(body, failure):
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: httpx2.Response(400, json=body))) as client:
        provider = LangfuseTraceQueryProvider(
            client, base_url="https://langfuse.example", public_key="pk", secret_key="sk"
        )
        with pytest.raises(TraceQueryProviderError) as caught:
            await provider.list_traces(query())
        assert caught.value.failure == failure


@pytest.mark.anyio
async def test_exact_langfuse_read_is_scoped_and_does_not_download_children():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json={"data": [root()], "meta": {"cursor": None}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        provider = LangfuseTraceQueryProvider(
            client, base_url="https://langfuse.example", public_key="pk", secret_key="sk"
        )
        await provider.get_trace(replace(read_query(), history_from=datetime(2020, 1, 1, tzinfo=UTC)))
    assert len(calls) == 1 and calls[0].url.params["limit"] == "2"
    filters = json.loads(calls[0].url.params["filter"])
    assert {item.get("key") for item in filters} >= {"attributes.a13n.organization.id", "attributes.a13n.workspace.id"}
    assert any(item["value"] == "2020-01-01T00:00:00Z" for item in filters)
