from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from a13n_harness import (
    AgentIdentityRef,
    AgentSpec,
    HarnessBuilder,
    HarnessExtensionEvent,
    HarnessInstrumentation,
    HarnessRunResultEvent,
    ModelRecoveryPolicy,
    RunBindings,
    RunError,
)
from a13n_harness.capabilities import Mem0Capability, Mem0Scope
from a13n_harness.capabilities import mem0 as mem0_module
from mem0 import AsyncMemoryClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio


class _FakeMem0Client(AsyncMemoryClient):
    def __init__(self) -> None:
        self.search_calls: list[tuple[str, dict[str, Any]]] = []
        self.list_calls: list[dict[str, Any]] = []
        self.add_calls: list[tuple[object, dict[str, Any]]] = []
        self.entered = 0
        self.exited = 0
        self.search_response: object = {
            "results": [
                {"memory": "The user prefers tea.", "score": 0.9},
            ]
        }
        self.search_error: BaseException | None = None
        self.exit_error: BaseException | None = None

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        del exc_type, exc_val, exc_tb
        self.exited += 1
        if self.exit_error is not None:
            raise self.exit_error

    async def search(self, query, options=None, **kwargs):
        del options
        self.search_calls.append((query, kwargs))
        if self.search_error is not None:
            raise self.search_error
        return self.search_response

    async def get_all(self, options=None, **kwargs):
        del options
        self.list_calls.append(kwargs)
        return {"results": [{"memory": "Listed memory"}]}

    async def add(self, messages, options=None, **kwargs):
        del options
        self.add_calls.append((messages, kwargs))
        return {"results": []}


def _bindings() -> RunBindings:
    return RunBindings.embedded(
        identity=AgentIdentityRef(
            issuer="test",
            subject="subject-1",
            agent_id="agent-1",
            user_id="user-1",
        )
    )


def _metric_map(reader: InMemoryMetricReader) -> dict[str, Any]:
    data = reader.get_metrics_data()
    return {
        metric.name: metric
        for resource_metrics in data.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }


async def test_auto_recall_is_once_per_logical_run_and_persists_input_overlays() -> None:
    client = _FakeMem0Client()
    calls: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        calls.append(messages)
        if len(calls) == 1:
            raise UnexpectedModelBehavior("retry")
        yield "done"

    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    executable = HarnessBuilder(
        instrumentation=HarnessInstrumentation(
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
        )
    ).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Mem0Capability(client=client, toolset=False),),
        model_recovery=ModelRecoveryPolicy(
            enabled=True,
            max_attempts=2,
            backoff_initial_seconds=0,
            backoff_max_seconds=0,
        ),
    )

    events: list[HarnessExtensionEvent] = []
    result = None
    async with executable.stream("What do I prefer?", bindings=_bindings()) as run:
        async for item in run:
            if isinstance(item, HarnessRunResultEvent):
                result = item.result
            elif isinstance(item.event, HarnessExtensionEvent):
                events.append(item.event)

    assert result is not None
    assert result.output_or_raise() == "done"
    assert len(client.search_calls) == 1
    query, kwargs = client.search_calls[0]
    assert query == "What do I prefer?"
    assert kwargs == {
        "filters": {
            "OR": [
                {"run_id": result.thread_id},
                {"agent_id": "agent-1"},
                {"user_id": "user-1"},
            ]
        },
        "top_k": 5,
    }
    assert len(calls) == 2
    assert all("The user prefers tea." in str(messages) for messages in calls)
    assert "The user prefers tea." in str(result.all_messages())
    assert client.entered == 0
    assert client.exited == 0

    recall_events = [
        event.payload for event in events if str(event.payload.get("type", "")).startswith("memory_recall")
    ]
    assert [payload["type"] for payload in recall_events] == [
        "memory_recall_started",
        "memory_recall_completed",
    ]
    assert recall_events[1]["result_count"] == 1
    assert "The user prefers tea." not in str(recall_events)
    operation_spans = [
        span
        for span in exporter.get_finished_spans()
        if span.name == "harness.operation" and span.attributes.get("a13n.operation.kind") == "memory_recall"
    ]
    assert len(operation_spans) == 1
    assert operation_spans[0].attributes["a13n.operation.kind"] == "memory_recall"
    assert operation_spans[0].attributes["a13n.memory_recall.result_count"] == 1
    assert operation_spans[0].attributes["langfuse.observation.metadata.memory_recall_limit"] == 5
    assert operation_spans[0].attributes["a13n.operation.status"] == "completed"
    assert operation_spans[0].attributes["a13n.output.capture"] == "captured"
    assert "langfuse.session.id" in operation_spans[0].attributes
    assert "The user prefers tea." not in str(operation_spans[0].attributes)
    points = _metric_map(reader)["a13n.harness.operation.duration"].data.data_points
    assert any(dict(point.attributes) == {"a13n.operation.kind": "memory_recall"} for point in points)


@pytest.mark.parametrize(
    ("scope", "expected_scope_property"),
    [
        (Mem0Scope.USER, False),
        (None, True),
    ],
)
async def test_toolset_schema_is_fixed_or_model_selectable(
    scope: Mem0Scope | None,
    expected_scope_property: bool,
) -> None:
    client = _FakeMem0Client()
    schemas: dict[str, dict[str, Any]] = {}

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        schemas.update({tool.name: tool.parameters_json_schema for tool in info.function_tools})
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Mem0Capability(client=client, scope=scope, auto_recall=False),),
    )
    result = await executable.run("hello", bindings=_bindings())

    assert result.output_or_raise() == "done"
    assert set(schemas) >= {"memory_search", "memory_list", "memory_add"}
    for name in ("memory_search", "memory_list", "memory_add"):
        assert ("scope" in schemas[name]["properties"]) is expected_scope_property
    if expected_scope_property:
        assert set(schemas["memory_search"]["$defs"]["Mem0Scope"]["enum"]) == {
            "thread",
            "agent",
            "user",
        }


async def test_fixed_scope_tools_resolve_ids_in_trusted_code_and_add_without_inference() -> None:
    client = _FakeMem0Client()
    model_calls = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal model_calls
        del messages, info
        model_calls += 1
        if model_calls == 1:
            yield {
                0: DeltaToolCall(
                    name="memory_search",
                    json_args=json.dumps({"query": "preference", "limit": 3}),
                    tool_call_id="memory-search-1",
                ),
                1: DeltaToolCall(
                    name="memory_list",
                    json_args=json.dumps({"limit": 4}),
                    tool_call_id="memory-list-1",
                ),
                2: DeltaToolCall(
                    name="memory_add",
                    json_args=json.dumps({"text": "The user prefers green tea."}),
                    tool_call_id="memory-add-1",
                ),
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            Mem0Capability(
                client=client,
                scope=Mem0Scope.USER,
                auto_recall=False,
            ),
        ),
    )
    result = await executable.run("remember", bindings=_bindings())

    assert result.output_or_raise() == "done"
    assert client.search_calls == [("preference", {"filters": {"user_id": "user-1"}, "top_k": 3})]
    assert client.list_calls == [{"filters": {"user_id": "user-1"}, "page": 1, "page_size": 4}]
    assert client.add_calls == [("The user prefers green tea.", {"filters": {"user_id": "user-1"}, "infer": False})]


async def test_owned_client_is_entered_and_closed_for_each_logical_run(monkeypatch: pytest.MonkeyPatch) -> None:
    clients: list[_FakeMem0Client] = []

    async def create_client() -> AsyncMemoryClient:
        client = _FakeMem0Client()
        clients.append(client)
        return client

    monkeypatch.setattr(mem0_module, "_create_owned_client", create_client)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Mem0Capability(auto_recall=False),),
    )
    for _ in range(2):
        result = await executable.run("hello", bindings=_bindings())
        assert result.output_or_raise() == "done"

    assert len(clients) == 2
    assert [(client.entered, client.exited) for client in clients] == [(1, 1), (1, 1)]


async def test_owned_client_uses_environment_without_eager_remote_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_eager_validation(client: AsyncMemoryClient) -> None:
        del client
        raise AssertionError("the upstream synchronous ping must not run")

    monkeypatch.setenv("MEM0_API_KEY", "test-key")
    monkeypatch.setenv("MEM0_BASE_URL", "https://mem0.example.test")
    monkeypatch.setattr(AsyncMemoryClient, "_validate_api_key", fail_eager_validation)

    created = await mem0_module._create_owned_client()
    try:
        assert isinstance(created, AsyncMemoryClient)
        assert created.api_key == "test-key"
        assert created.host == "https://mem0.example.test"
    finally:
        await created.__aexit__(None, None, None)


async def test_owned_client_supported_requests_never_include_deferred_project_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, dict[str, str], object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(
            (
                request.url.path,
                dict(request.url.params),
                json.loads(request.content),
            )
        )
        if request.url.path.endswith("/search/"):
            return httpx.Response(200, json={"results": [{"memory": "found"}]})
        if request.url.path.endswith("/memories/") and request.method == "POST" and request.url.params:
            return httpx.Response(200, json={"results": [{"memory": "listed"}]})
        return httpx.Response(200, json={"results": []})

    monkeypatch.setenv("MEM0_API_KEY", "test-key")
    monkeypatch.setenv("MEM0_BASE_URL", "https://mem0.example.test")
    client = await mem0_module._create_owned_client()
    await client.async_client.aclose()
    client.async_client = httpx.AsyncClient(
        base_url=client.host,
        transport=httpx.MockTransport(handle),
    )
    try:
        await client.search("query", filters={"user_id": "user-1"}, top_k=3)
        await client.get_all(filters={"user_id": "user-1"}, page=1, page_size=4)
        await client.add("memory", filters={"user_id": "user-1"}, infer=False)
    finally:
        await client.__aexit__(None, None, None)

    assert requests == [
        (
            "/v3/memories/search/",
            {},
            {"query": "query", "filters": {"user_id": "user-1"}, "top_k": 3},
        ),
        (
            "/v3/memories/",
            {"page": "1", "page_size": "4"},
            {"filters": {"user_id": "user-1"}},
        ),
        (
            "/v3/memories/add/",
            {},
            {
                "messages": [{"role": "user", "content": "memory"}],
                "filters": {"user_id": "user-1"},
                "infer": False,
            },
        ),
    ]
    assert "deferred" not in json.dumps(requests)


async def test_owned_client_construction_closes_late_result_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeMem0Client()
    client.exit_error = RuntimeError("provider secret")
    started = asyncio.Event()
    release = asyncio.Event()

    async def to_thread(function, /, *args, **kwargs):
        assert function is mem0_module._RunOwnedAsyncMemoryClient
        assert args == ()
        assert kwargs == {"api_key": "test-key", "host": None}
        started.set()
        await release.wait()
        return client

    monkeypatch.setenv("MEM0_API_KEY", "test-key")
    monkeypatch.delenv("MEM0_BASE_URL", raising=False)
    monkeypatch.setattr(asyncio, "to_thread", to_thread)

    construction = asyncio.create_task(mem0_module._create_owned_client())
    await started.wait()
    construction.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await construction
    assert client.exited == 1
    assert exc_info.value.__notes__ == ["Run-owned Mem0 client cleanup also failed with RuntimeError."]
    assert "provider secret" not in str(exc_info.value.__notes__)


async def test_owned_client_is_closed_when_required_recall_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeMem0Client()
    client.search_error = RuntimeError("provider secret")

    async def create_client() -> AsyncMemoryClient:
        return client

    monkeypatch.setattr(mem0_module, "_create_owned_client", create_client)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "unreachable"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            Mem0Capability(
                toolset=False,
                recall_required=True,
            ),
        ),
    )

    with pytest.raises(RunError) as exc_info:
        await executable.run("hello", bindings=_bindings())

    assert exc_info.value.code == "mem0_recall_failed"
    assert (client.entered, client.exited) == (1, 1)


async def test_invalid_unicode_recall_fails_open_before_overlay_projection() -> None:
    client = _FakeMem0Client()
    client.search_response = {"results": [{"memory": "bad\ud800"}]}
    model_called = False

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal model_called
        del messages, info
        model_called = True
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Mem0Capability(client=client, toolset=False),),
    )
    events: list[HarnessExtensionEvent] = []
    result = None
    async with executable.stream("hello", bindings=_bindings()) as run:
        async for item in run:
            if isinstance(item, HarnessRunResultEvent):
                result = item.result
            elif isinstance(item.event, HarnessExtensionEvent):
                events.append(item.event)

    assert result is not None
    assert result.output_or_raise() == "done"
    assert model_called is True
    failed = next(event.payload for event in events if event.payload.get("type") == "memory_recall_failed")
    assert failed["error_code"] == "mem0_response_invalid"
    assert failed["retryable"] is False
    assert all(event.payload.get("type") != "memory_recall_completed" for event in events)


async def test_recalled_memory_is_bounded_by_utf8_bytes() -> None:
    memories = mem0_module._normalize_memories(
        {"results": [{"memory": "界" * 3_000}]},
        limit=1,
    )

    assert len(memories) == 1
    assert len(memories[0].memory.encode("utf-8")) <= 8_000
    assert memories[0].memory


async def test_optional_recall_failure_is_observed_while_required_recall_fails() -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    optional_client = _FakeMem0Client()
    optional_client.search_error = RuntimeError("provider secret")
    optional = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Mem0Capability(client=optional_client, toolset=False),),
    )
    optional_events: list[HarnessExtensionEvent] = []
    optional_result = None
    async with optional.stream("hello", bindings=_bindings()) as run:
        async for item in run:
            if isinstance(item, HarnessRunResultEvent):
                optional_result = item.result
            elif isinstance(item.event, HarnessExtensionEvent):
                optional_events.append(item.event)
    assert optional_result is not None
    assert optional_result.output_or_raise() == "done"
    failed = next(event.payload for event in optional_events if event.payload.get("type") == "memory_recall_failed")
    assert failed["error_code"] == "mem0_recall_failed"
    assert "provider secret" not in str(failed)

    required_client = _FakeMem0Client()
    required_client.search_error = RuntimeError("provider secret")
    required = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            Mem0Capability(
                client=required_client,
                toolset=False,
                recall_required=True,
            ),
        ),
    )
    with pytest.raises(RunError) as exc_info:
        await required.run("hello", bindings=_bindings())
    assert exc_info.value.code == "mem0_recall_failed"
    assert "provider secret" not in str(exc_info.value)
