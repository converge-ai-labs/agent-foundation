from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx2
import pytest
from a13n_service.trace_query import (
    LangfuseTraceQueryProvider,
    ProviderTraceQuery,
    SearchIn,
    TraceQueryProviderError,
    TraceView,
)


def root(*, trace_id: str = "trace-1") -> dict[str, object]:
    return {
        "id": "root-1",
        "traceId": trace_id,
        "startTime": "2026-09-01T01:02:03.000Z",
        "endTime": "2026-09-01T01:02:04.250Z",
        "projectId": "project-1",
        "parentObservationId": None,
        "isRootObservation": True,
        "type": "SPAN",
        "name": "a13n.service.run_attempt",
        "level": "DEFAULT",
        "input": '{"prompt":"hello"}',
        "output": '{"answer":"world"}',
        "providedModelName": None,
        "usageDetails": {},
        "totalCost": None,
        "metadata": {
            "attributes": {
                "a13n.organization.id": "org-1",
                "a13n.workspace.id": "ws-1",
                "a13n.observation.session.id": "session-1",
                "session.id": "thread-1",
                "a13n.thread.id": "thread-1",
                "a13n.service.run.id": "run-1",
                "a13n.run_attempt.id": "attempt-1",
                "a13n.agent.preset.id": "agent-1",
                "input.mime_type": "application/json",
                "output.mime_type": "application/json",
            },
            "resourceAttributes": {"service.name": "a13n-service"},
            "scope": {"name": "a13n-a13n-service"},
        },
    }


def child() -> dict[str, object]:
    return {
        "id": "generation-1",
        "traceId": "trace-1",
        "startTime": "2026-09-01T01:02:03.100Z",
        "endTime": "2026-09-01T01:02:03.900Z",
        "projectId": "project-1",
        "parentObservationId": "root-1",
        "isRootObservation": False,
        "type": "GENERATION",
        "name": "chat model",
        "level": "DEFAULT",
        "input": "hello",
        "output": "world",
        "model": "gpt-test",
        "usageDetails": {"input": 12, "output": 4, "total": 16},
        "totalCost": 0.00125,
        "metadata": {
            "attributes": {
                "a13n.organization.id": "org-1",
                "a13n.workspace.id": "ws-1",
                "a13n.run_attempt.id": "attempt-1",
                "input.mime_type": "text/plain",
                "output.mime_type": "text/plain",
            },
            "resourceAttributes": {"service.name": "a13n-service"},
            "scope": {"name": "pydantic-ai"},
        },
    }


def query(**updates: object) -> ProviderTraceQuery:
    values: dict[str, object] = {
        "organization_id": "org-1",
        "workspace_id": "ws-1",
        "from_started_at": datetime(2026, 9, 1, tzinfo=UTC),
        "to_started_at": datetime(2026, 9, 2, tzinfo=UTC),
        "limit": 50,
    }
    values.update(updates)
    return ProviderTraceQuery(**values)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_list_uses_v2_root_filters_and_normalizes_correlation() -> None:
    captured: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        return httpx2.Response(200, json={"data": [root()], "meta": {"cursor": "provider-next"}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        provider = LangfuseTraceQueryProvider(
            client,
            base_url="https://langfuse.example.com/",
            public_key="pk-test",
            secret_key="sk-test",
        )
        page = await provider.list_traces(
            query(
                query="hello world",
                search_in=SearchIn.input,
                thread_id="thread-1",
                run_id="run-1",
                run_attempt_id="attempt-1",
            )
        )

    assert page.next_cursor == "provider-next"
    assert len(page.items) == 1
    summary = page.items[0]
    assert summary.id == "trace-1"
    assert summary.duration_ms == 1250
    assert summary.correlation.thread_id == "thread-1"
    assert summary.correlation.run_attempt_id == "attempt-1"
    assert summary.input == {"prompt": "hello"}
    assert summary.output == {"answer": "world"}
    assert summary.source_url == "https://langfuse.example.com/project/project-1/traces/trace-1"

    request = captured[0]
    assert request.url.path == "/api/public/v2/observations"
    assert request.headers["authorization"].startswith("Basic ")
    assert request.url.params["expandMetadata"] == "attributes,resourceAttributes,scope"
    filters = json.loads(request.url.params["filter"])
    assert {item["column"] for item in filters} >= {
        "name",
        "isRootObservation",
        "startTime",
        "sessionId",
        "input",
        "metadata",
    }
    assert {(item.get("key"), item["value"]) for item in filters if item["column"] == "metadata"} == {
        ("attributes.a13n.organization.id", "org-1"),
        ("attributes.a13n.workspace.id", "ws-1"),
        ("attributes.a13n.service.run.id", "run-1"),
        ("attributes.a13n.run_attempt.id", "attempt-1"),
    }


@pytest.mark.anyio
async def test_list_rejects_cross_workspace_data_returned_by_provider() -> None:
    other = root(trace_id="trace-other")
    attributes = other["metadata"]["attributes"]  # type: ignore[index]
    attributes["a13n.workspace.id"] = "ws-other"  # type: ignore[index]

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda _request: httpx2.Response(
                200,
                json={"data": [other, root()], "meta": {"cursor": "provider-next"}},
            )
        )
    ) as client:
        provider = LangfuseTraceQueryProvider(
            client,
            base_url="https://langfuse.example.com",
            public_key="pk-test",
            secret_key="sk-test",
        )
        page = await provider.list_traces(query())

    assert [item.id for item in page.items] == ["trace-1"]
    assert page.next_cursor == "provider-next"


@pytest.mark.anyio
async def test_list_accepts_flattened_v4_metadata_response() -> None:
    item = root()
    metadata = item["metadata"]
    assert isinstance(metadata, dict)
    flattened: dict[str, object] = {}
    for namespace, values in metadata.items():
        assert isinstance(values, dict)
        flattened.update({f"{namespace}.{key}": value for key, value in values.items()})
    item["metadata"] = flattened

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda _request: httpx2.Response(200, json={"data": [item], "meta": {"cursor": None}})
        )
    ) as client:
        provider = LangfuseTraceQueryProvider(
            client,
            base_url="https://langfuse.example.com",
            public_key="pk-test",
            secret_key="sk-test",
        )
        page = await provider.list_traces(query())

    assert page.items[0].correlation.workspace_id == "ws-1"
    assert page.items[0].input == {"prompt": "hello"}


@pytest.mark.anyio
async def test_detail_groups_observations_and_compact_omits_content() -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"data": [child(), root()], "meta": {"cursor": None}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        provider = LangfuseTraceQueryProvider(
            client,
            base_url="https://langfuse.example.com",
            public_key="pk-test",
            secret_key="sk-test",
        )
        full = await provider.get_trace("trace-1", TraceView.full)
        compact = await provider.get_trace("trace-1", TraceView.compact)

    assert full is not None
    assert full.trace.observation_count == 2
    assert [item.id for item in full.observations] == ["root-1", "generation-1"]
    generation = full.observations[1]
    assert generation.type == "generation"
    assert generation.model == "gpt-test"
    assert generation.usage == {"input": 12, "output": 4, "total": 16}
    assert str(generation.cost_usd) == "0.00125"
    assert generation.input == "hello"
    assert generation.metadata["scope"] == {"name": "pydantic-ai"}
    assert compact is not None
    assert compact.observations[0].input is None
    assert compact.observations[0].output is None
    assert compact.observations[0].metadata == {}
    assert requests[0].url.params["fields"].find("io") >= 0
    assert "io" not in requests[1].url.params["fields"]
    assert "metadata" in requests[1].url.params["fields"]
    assert requests[1].url.params["expandMetadata"] == "attributes,resourceAttributes,scope"


@pytest.mark.anyio
async def test_missing_root_is_absent_and_combined_search_fails_explicitly() -> None:
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda _request: httpx2.Response(200, json={"data": [child()], "meta": {"cursor": None}})
        )
    ) as client:
        provider = LangfuseTraceQueryProvider(
            client,
            base_url="https://langfuse.example.com",
            public_key="pk-test",
            secret_key="sk-test",
        )
        assert await provider.get_trace("trace-1", TraceView.full) is None
        with pytest.raises(TraceQueryProviderError) as raised:
            await provider.list_traces(query(query="hello", search_in=SearchIn.input_output))
        assert raised.value.failure == "filter_unsupported"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("response", "failure"),
    [
        (httpx2.Response(404), "version_unsupported"),
        (httpx2.Response(401), "unavailable"),
        (httpx2.Response(200, content=b"not-json"), "malformed"),
    ],
)
async def test_provider_failures_are_safe(response: httpx2.Response, failure: str) -> None:
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: response)) as client:
        provider = LangfuseTraceQueryProvider(
            client,
            base_url="https://langfuse.example.com",
            public_key="pk-test",
            secret_key="sk-test",
        )
        with pytest.raises(TraceQueryProviderError) as raised:
            await provider.list_traces(query())
        assert raised.value.failure == failure


def test_langfuse_configuration_rejects_credential_bearing_url() -> None:
    with pytest.raises(ValueError, match="base URL"):
        LangfuseTraceQueryProvider(
            httpx2.AsyncClient(),
            base_url="https://user:password@langfuse.example.com",
            public_key="pk-test",
            secret_key="sk-test",
        )


@pytest.mark.anyio
async def test_list_rejects_a_provider_page_larger_than_requested() -> None:
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda _request: httpx2.Response(
                200,
                json={"data": [root(), root(trace_id="trace-2")], "meta": {"cursor": None}},
            )
        )
    ) as client:
        provider = LangfuseTraceQueryProvider(
            client,
            base_url="https://langfuse.example.com",
            public_key="pk-test",
            secret_key="sk-test",
        )
        with pytest.raises(TraceQueryProviderError) as raised:
            await provider.list_traces(query(limit=1))

    assert raised.value.failure == "response_too_large"


@pytest.mark.anyio
async def test_provider_classifies_invalid_remote_text_as_malformed() -> None:
    invalid_root = root(trace_id="trace-1\x00private")
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda _request: httpx2.Response(
                200,
                json={"data": [invalid_root], "meta": {"cursor": None}},
            )
        )
    ) as client:
        provider = LangfuseTraceQueryProvider(
            client,
            base_url="https://langfuse.example.com",
            public_key="pk-test",
            secret_key="sk-test",
        )
        with pytest.raises(TraceQueryProviderError) as raised:
            await provider.list_traces(query())

    assert raised.value.failure == "malformed"
