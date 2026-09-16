from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import httpx2
import pytest
from a13n_service.settings import Settings
from a13n_service.trace_query.domain import (
    ProviderTraceQuery,
    ProviderTraceRead,
    SearchIn,
    TraceView,
)
from a13n_service.trace_query.errors import TraceQueryProviderError
from a13n_service.trace_query.logfire import LogfireTraceQueryProvider, _observation

START = datetime(2026, 9, 1, tzinfo=UTC)
END = datetime(2026, 9, 2, tzinfo=UTC)


def row(**updates):
    return {
        "trace_id": "trace-1",
        "span_id": "root-1",
        "parent_span_id": None,
        "span_name": "a13n.service.run_attempt",
        "start_timestamp": "2026-09-01T01:00:00.123456789Z",
        "end_timestamp": "2026-09-01T01:00:01Z",
        "otel_status_code": 0,
        "level": "info",
        "otel_status_message": None,
        "attributes": {
            "a13n.organization.id": "org-1",
            "a13n.workspace.id": "ws-1",
            "a13n.observation.session.id": "session-1",
            "a13n.thread.id": "thread-1",
            "a13n.service.run.id": "run-1",
            "a13n.run_attempt.id": "attempt-1",
            "a13n.agent.preset.id": "agent-1",
            "openinference.span.kind": "AGENT",
            "gen_ai.request.model": "alias",
            "gen_ai.response.model": "version",
            "gen_ai.usage.input_tokens": 0,
            "gen_ai.usage.output_tokens": 5,
            "gen_ai.usage.cost": 0,
            "input.value": None,
            "input.mime_type": "application/json",
        },
        "otel_resource_attributes": '{"service.name":"service"}',
        "otel_scope_name": "library",
        "otel_scope_version": "1",
        "otel_scope_attributes": {},
        "otel_events": [
            {
                "event_name": "exception",
                "event_timestamp": "2026-09-01T01:00:01Z",
                "attributes": {"exception.message": "failed"},
            }
        ],
        "otel_links": [
            {
                "context": {"trace_id": "other-trace", "span_id": "other-span"},
                "attributes": {},
            }
        ],
        **updates,
    }


def provider(client):
    return LogfireTraceQueryProvider(
        client,
        base_url="https://logfire.example",
        read_token="test-read-token",
        history_from=START,
    )


def read():
    return ProviderTraceRead(
        organization_id="org-1",
        workspace_id="ws-1",
        trace_id="trace-1",
        history_from=START,
        to_started_at=END,
        view=TraceView.full,
    )


def test_mapping_preserves_explicit_status_and_reported_values():
    observation = _observation(row(), TraceView.full)
    assert observation.status == "unset"
    assert observation.level == "info"
    assert observation.type == "agent"
    assert observation.model.requested == "alias"
    assert observation.model.response == "version"
    assert observation.usage == {
        "gen_ai.usage.input_tokens": 0,
        "gen_ai.usage.output_tokens": 5,
    }
    assert observation.cost_usd == 0
    assert observation.input.value is None
    assert observation.resource_attributes == {"service.name": "service"}
    assert observation.scope.attributes == {}
    assert observation.events[0].name == "exception"
    assert observation.events[0].occurred_at == datetime(2026, 9, 1, 1, 0, 1, tzinfo=UTC)
    assert observation.events[0].attributes["exception.message"] == "failed"
    assert observation.links[0].observation_id == "other-span"
    compact = _observation(row(), TraceView.compact)
    assert compact.input is compact.attributes is compact.scope is compact.events is None
    assert compact.links[0].attributes is None


@pytest.mark.parametrize(
    "value,expected",
    [(None, None), (0, "unset"), (1, "ok"), (2, "error"), ("ERROR", "error")],
)
def test_status_is_not_inferred_from_level_or_end(value, expected):
    assert _observation(row(otel_status_code=value, level="error"), TraceView.full).status == expected


def test_unknown_and_empty_attached_values():
    assert _observation(row(otel_events=None, otel_links=None), TraceView.full).events is None
    observation = _observation(row(otel_events="[]", otel_links=[]), TraceView.full)
    assert observation.events == observation.links == ()


@pytest.mark.anyio
async def test_scoped_sql_literal_search_and_precise_keyset():
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-read-token"
        assert request.url.path == "/v2/query"
        return httpx2.Response(
            200,
            json={
                "schema": {"fields": []},
                "data": [row(), row(span_id="root-0")] if len(requests) == 1 else [],
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        adapter = provider(client)
        query = ProviderTraceQuery(
            organization_id="org-1",
            workspace_id="ws-1",
            from_started_at=START,
            to_started_at=END,
            limit=1,
            query="x' OR true --",
            search_in=SearchIn.input_output,
            metadata=(("scenario", "rev'iew"),),
        )
        first = await adapter.list_traces(query)
        assert first.next_cursor is not None
        assert "123456789Z" in first.next_cursor
        assert len(first.items) == 1
        assert (await adapter.list_traces(replace(query, cursor=first.next_cursor))).next_cursor is None
    sql = requests[0]["sql"]
    assert "FROM records WHERE kind = 'span'" in sql
    assert "parent_span_id IS NULL" in sql
    assert "attributes->>'a13n.organization.id' = 'org-1'" in sql
    assert "x'' OR true --" in sql
    assert "attributes->>'gen_ai.input.messages'" in sql
    assert "attributes->>'a13n.output'" in sql
    assert "attributes->>'a13n.observation.metadata.scenario' = 'rev''iew'" in sql
    assert "ORDER BY start_timestamp DESC, trace_id DESC, span_id DESC LIMIT 2" in sql
    assert "start_timestamp = '2026-09-01T01:00:00.123456789Z'" in requests[1]["sql"]
    assert requests[0]["min_timestamp"] == "2026-09-01T00:00:00Z"


@pytest.mark.anyio
async def test_exact_root_and_children_do_not_require_repeated_correlations():
    requests = []

    def handle(request):
        requests.append(json.loads(request.content)["sql"])
        return httpx2.Response(
            200,
            json={
                "data": (
                    [row()] if len(requests) == 1 else [row(span_id="child", parent_span_id="root-1", attributes={})]
                )
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        adapter = provider(client)
        assert (await adapter.get_trace(read())).root.id == "root-1"
        assert (await adapter.list_observations(read())).items[0].parent_id == "root-1"
    assert "a13n.organization.id" in requests[0]
    assert "a13n.organization.id" not in requests[1]
    assert "trace_id = 'trace-1'" in requests[1]


@pytest.mark.anyio
async def test_ambiguous_root_is_rejected():
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json={"data": [row(), row(span_id="other")]}))
    ) as client:
        with pytest.raises(TraceQueryProviderError, match="malformed"):
            await provider(client).get_trace(read())


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status,failure",
    [
        (400, "unavailable"),
        (401, "unavailable"),
        (404, "version_unsupported"),
        (500, "unavailable"),
    ],
)
async def test_safe_provider_errors(status, failure):
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(status, text="private diagnostics"))
    ) as client:
        with pytest.raises(TraceQueryProviderError, match=failure):
            await provider(client).get_trace(read())


def test_logfire_configuration_and_credential_fingerprint():
    values = {
        "provider": "logfire",
        "logfire_base_url": "https://logfire.example",
        "logfire_read_token": "test-secret",
        "logfire_history_from": START,
    }
    Settings(observability={"query": values}).validate_trace_query_configuration()
    assert "test-secret" not in repr(Settings(observability={"query": values}))
    with pytest.raises(ValueError, match="timezone-aware"):
        Settings(
            observability={"query": {**values, "logfire_history_from": START.replace(tzinfo=None)}}
        ).validate_trace_query_configuration()
    with pytest.raises(ValueError, match="READ_TOKEN"):
        Settings(observability={"query": {**values, "logfire_read_token": None}}).validate_trace_query_configuration()


@pytest.mark.parametrize(
    "operation,category",
    [
        ("invoke_agent", "agent"),
        ("chat", "generation"),
        ("execute_tool", "tool"),
        ("future_operation", "future_operation"),
    ],
)
def test_native_harness_categories_and_message_containers(operation, category):
    messages = [{"role": "user", "parts": [{"type": "text", "content": "hello"}]}]
    item = _observation(
        row(
            attributes={
                "gen_ai.operation.name": operation,
                "gen_ai.input.messages": messages,
                "gen_ai.output.messages": [],
            }
        ),
        TraceView.full,
    )
    assert item.type == category
    assert item.input.value == messages
    assert item.output.value == []
    harness = _observation(
        row(
            attributes={
                "langfuse.observation.type": "agent",
                "a13n.input": {"prompt": "hello"},
                "a13n.output": "answer",
            }
        ),
        TraceView.full,
    )
    assert harness.type == "agent"
    assert harness.input.value == {"prompt": "hello"}
    assert harness.output.value == "answer"


@pytest.mark.parametrize("key", ["input.value", "a13n.input", "gen_ai.input.messages"])
@pytest.mark.parametrize(
    "value",
    [
        "plain text",
        "null",
        "123",
        '"quoted"',
        '{"text":1}',
        None,
        [],
        {"text": "hello"},
    ],
)
def test_logfire_content_is_already_decoded_and_present_null_is_not_missing(key, value):
    attributes = {key: value, "input.mime_type": "application/json"}
    item = _observation(row(attributes=attributes), TraceView.full)
    assert item.input is not None
    assert item.input.media_type == "application/json"
    assert item.input.value == value
    assert _observation(row(attributes={}), TraceView.full).input is None


@pytest.mark.parametrize("serialized", [False, True])
def test_native_events_preserve_sequence_and_reject_invalid_entries(serialized):
    event = {
        "event_name": "exception",
        "event_timestamp": "2026-09-01T01:00:01Z",
        "attributes": {"exception.message": "failed"},
    }
    events = [event, event]
    item = _observation(row(otel_events=json.dumps(events) if serialized else events), TraceView.full)
    assert len(item.events) == 2 and item.events[0] == item.events[1]
    with pytest.raises(TraceQueryProviderError, match="malformed"):
        _observation(
            row(otel_events=[{**event, "event_timestamp": "not-a-timestamp"}]),
            TraceView.full,
        )


@pytest.mark.anyio
async def test_compact_projects_attributes_before_transport_and_keeps_open_usage():
    from a13n_service.trace_query.logfire import _COMPACT_ATTRIBUTE_KEYS

    full = row()
    full["attributes"].update(
        {
            "output.value": "x" * 300_000,
            "arbitrary.body": "x" * 300_000,
            "gen_ai.usage.custom_detail": 9007199254740993,
            'gen_ai.usage.quoted"key': 25,
        }
    )
    requests = []

    def handle(request):
        sql = json.loads(request.content)["sql"]
        requests.append(sql)
        if sql.startswith("WITH page AS"):
            assert "UNNEST(json_object_keys(attributes))" in sql
            assert "starts_with(attribute_key, 'gen_ai.usage.')" in sql
            assert "LEFT JOIN compact" in sql
            assert "json_get_json(attributes, attribute_key)" in sql
            assert "ORDER BY page.start_timestamp DESC, page.trace_id DESC, page.span_id DESC" in sql
            assert "page.attributes" not in sql
            projected = {
                key: value
                for key, value in full["attributes"].items()
                if key in _COMPACT_ATTRIBUTE_KEYS or key.startswith("gen_ai.usage.")
            }
            return httpx2.Response(200, json={"data": [row(attributes=json.dumps(projected))]})
        return httpx2.Response(200, json={"data": [full]})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        adapter = provider(client)
        compact = await adapter.get_trace(replace(read(), view=TraceView.compact))
        assert compact.correlation.workspace_id == "ws-1"
        assert compact.root.input is compact.root.output is compact.root.attributes is None
        assert compact.root.usage["gen_ai.usage.custom_detail"] == 9007199254740993
        assert compact.root.usage['gen_ai.usage.quoted"key'] == 25
        assert compact.root.cost_usd == 0
        with pytest.raises(TraceQueryProviderError, match="response_too_large"):
            await adapter.get_trace(read())
    assert "LIMIT 3" in requests[0]
    assert not requests[1].startswith("WITH page AS")
