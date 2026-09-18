from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

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
from a13n_harness.capabilities import MemoryCapability, MemoryScope
from a13n_harness.capabilities import memory as memory_module
from a13n_harness.capabilities.mem0_backends import Mem0PlatformBackend
from a13n_harness.memory import MemoryRecord, MemorySubject
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
        filters = kwargs["filters"]
        subjects = filters.get("OR", [filters])
        return {
            **self.search_response,
            "results": [{"id": "memory-1", **subjects[0], **item} for item in self.search_response["results"]],
        }

    async def get_all(self, options=None, **kwargs):
        del options
        self.list_calls.append(kwargs)
        return {"results": [{"id": "memory-1", "memory": "Listed memory", **kwargs["filters"]}]}

    async def add(self, messages, options=None, **kwargs):
        del options
        self.add_calls.append((messages, kwargs))
        self.stored = {"id": "memory-1", "memory": messages, **kwargs["filters"]}
        return {"results": [{"id": "memory-1", "event": "ADD", "memory": messages}]}

    async def get(self, memory_id):
        assert memory_id == "memory-1"
        return self.stored


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
            raise UnexpectedModelBehavior("Streamed response ended without content or tool calls")
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
        capabilities=(MemoryCapability(backend=Mem0PlatformBackend(client), toolset=False),),
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
        (MemoryScope.USER, False),
        (None, True),
    ],
)
async def test_toolset_schema_is_fixed_or_model_selectable(
    scope: MemoryScope | None,
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
        capabilities=(MemoryCapability(backend=Mem0PlatformBackend(client), scope=scope, auto_recall=False),),
    )
    result = await executable.run("hello", bindings=_bindings())

    assert result.output_or_raise() == "done"
    assert set(schemas) >= {"memory_search", "memory_list", "memory_add"}
    for name in ("memory_search", "memory_list", "memory_add"):
        assert ("scope" in schemas[name]["properties"]) is expected_scope_property
    if expected_scope_property:
        assert set(schemas["memory_search"]["$defs"]["MemoryScope"]["enum"]) == {
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
            MemoryCapability(
                backend=Mem0PlatformBackend(client),
                scope=MemoryScope.USER,
                auto_recall=False,
            ),
        ),
    )
    result = await executable.run("remember", bindings=_bindings())

    assert result.output_or_raise() == "done"
    assert client.search_calls == [("preference", {"filters": {"user_id": "user-1"}, "top_k": 3})]
    assert client.list_calls == [{"filters": {"user_id": "user-1"}, "page": 1, "page_size": 4}]
    assert client.add_calls == [("The user prefers green tea.", {"filters": {"user_id": "user-1"}, "infer": False})]


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
        capabilities=(MemoryCapability(backend=Mem0PlatformBackend(client), toolset=False),),
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
    assert failed["error_code"] == "memory_response_invalid"
    assert failed["retryable"] is False
    assert all(event.payload.get("type") != "memory_recall_completed" for event in events)


async def test_recalled_memory_is_bounded_by_utf8_bytes() -> None:
    memories = memory_module._normalize_memories(
        (MemoryRecord("memory-1", "界" * 3_000, (MemorySubject(MemoryScope.USER, "user-1"),)),),
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
        capabilities=(MemoryCapability(backend=Mem0PlatformBackend(optional_client), toolset=False),),
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
    assert failed["error_code"] == "memory_recall_failed"
    assert "provider secret" not in str(failed)

    required_client = _FakeMem0Client()
    required_client.search_error = RuntimeError("provider secret")
    required = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            MemoryCapability(
                backend=Mem0PlatformBackend(required_client),
                toolset=False,
                recall_required=True,
            ),
        ),
    )
    with pytest.raises(RunError) as exc_info:
        await required.run("hello", bindings=_bindings())
    assert exc_info.value.code == "memory_recall_failed"
    assert "provider secret" not in str(exc_info.value)


async def test_custom_plugin_uses_public_current_run_memory_with_defaults_disabled():
    import asyncio
    from dataclasses import dataclass

    from a13n_harness import AbstractHarnessPlugin, DefinitionError
    from a13n_harness.memory import MemoryBackend, MemoryPage, MemoryRecordNotFound, require_memory_subject
    from pydantic_ai.capabilities import AbstractCapability

    class Backend(MemoryBackend):
        def __init__(self):
            self.records = {}
            self.calls = []

        async def search(self, query, *, subjects, limit, threshold=None):
            self.calls.append(("search", subjects))
            return tuple(record for record in self.records.values() if any(s in record.subjects for s in subjects))[
                :limit
            ]

        async def list(self, subject, *, limit, cursor=None):
            self.calls.append(("list", (subject,)))
            return MemoryPage(tuple(record for record in self.records.values() if subject in record.subjects)[:limit])

        async def add(self, text, *, subject):
            self.calls.append(("add", (subject,)))
            record = MemoryRecord(f"record-{len(self.records)}", text, (subject,))
            self.records[record.id] = record
            return record

        async def get(self, memory_id, *, subject):
            record = self.records.get(memory_id)
            if record is None:
                raise MemoryRecordNotFound(memory_id)
            require_memory_subject(record, (subject,))
            return record

        async def update(self, memory_id, text, *, subject):
            await self.get(memory_id, subject=subject)
            record = MemoryRecord(memory_id, text, (subject,))
            self.records[memory_id] = record
            return record

        async def delete(self, memory_id, *, subject):
            await self.get(memory_id, subject=subject)
            del self.records[memory_id]

    backend = Backend()
    source = MemoryCapability(backend=backend, auto_recall=False, toolset=False)
    contexts = []
    ready = asyncio.Event()

    @dataclass
    class CustomMemory(AbstractCapability):
        async def before_model_request(self, ctx, request_context):
            memory = ctx.capabilities.get(MemoryCapability.id)
            assert isinstance(memory, MemoryCapability)
            assert memory is not source
            contexts.append((ctx, memory))
            if len(contexts) == 2:
                ready.set()
            await ready.wait()
            other_memory = next(cap for other, cap in contexts if other is not ctx)
            assert other_memory is not memory
            with pytest.raises(DefinitionError):
                await other_memory.list(ctx, scope=MemoryScope.THREAD)
            with pytest.raises(DefinitionError):
                await source.list(ctx, scope=MemoryScope.THREAD)
            with pytest.raises(RunError):
                await memory.list(ctx)
            record = await memory.add(ctx, f"exact {ctx.deps.thread_id}", scope=MemoryScope.THREAD)
            assert record.subjects == (MemorySubject(MemoryScope.THREAD, ctx.deps.thread_id),)
            assert await memory.get(ctx, record.id, scope=MemoryScope.THREAD) == record
            updated = await memory.update(ctx, record.id, "updated", scope=MemoryScope.THREAD)
            assert updated.text == "updated"
            assert (await memory.list(ctx, scope=MemoryScope.THREAD)).items == (updated,)
            assert await memory.search(ctx, "query", scope=MemoryScope.THREAD) == (updated,)
            await memory.delete(ctx, record.id, scope=MemoryScope.THREAD)
            assert not (await memory.list(ctx, scope=MemoryScope.THREAD)).items
            assert all(not name.startswith("memory_") for name in ctx.available_tool_names)
            return request_context

    class Plugin(AbstractHarnessPlugin):
        @property
        def plugin_id(self):
            return "test.custom-memory"

        def get_capabilities(self):
            return (CustomMemory(),)

    async def stream(messages, info):
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(source,),
        plugins=(Plugin(),),
    )
    results = await asyncio.gather(
        executable.run("one", bindings=_bindings()),
        executable.run("two", bindings=_bindings()),
    )
    assert all(result.output_or_raise() == "done" for result in results)
    assert len(contexts) == 2
    assert len([call for call in backend.calls if call[0] == "search"]) == 2
    assert not backend.records
    assert all("memory-context" not in str(result.all_messages()) for result in results)


async def test_root_and_child_can_borrow_one_source_without_sharing_run_binding_or_recall():
    from dataclasses import dataclass

    from a13n_harness import AgentDefinition, SubagentDefinition
    from a13n_harness.capabilities import SubagentCapability
    from pydantic_ai.capabilities import AbstractCapability

    client = _FakeMem0Client()
    source = MemoryCapability(backend=Mem0PlatformBackend(client), scope=MemoryScope.THREAD, toolset=False)
    seen = []

    @dataclass
    class CaptureMemory(AbstractCapability):
        async def before_model_request(self, ctx, request_context):
            memory = ctx.capabilities.get(MemoryCapability.id)
            assert isinstance(memory, MemoryCapability)
            page = await memory.list(ctx)
            assert page.items[0].subjects == (MemorySubject(MemoryScope.THREAD, ctx.deps.thread_id),)
            seen.append((ctx.deps, memory))
            return request_context

    async def child_stream(messages, info):
        yield "child-done"

    parent_calls = 0

    async def parent_stream(messages, info):
        nonlocal parent_calls
        parent_calls += 1
        if parent_calls == 1:
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps({"subagent": "helper", "prompt": "inspect"}),
                    tool_call_id="delegate-1",
                )
            }
        else:
            yield "parent-done"

    child = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="memory-child",
        model=FunctionModel(stream_function=child_stream),
        capabilities=(source, CaptureMemory()),
    )
    parent = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="memory-parent",
        model=FunctionModel(stream_function=parent_stream),
        capabilities=(source, CaptureMemory(), SubagentCapability()),
        subagents=(SubagentDefinition(name="helper", description="Inspect memory", agent=child),),
    )
    result = await HarnessBuilder().build(parent).run("delegate now", bindings=_bindings())
    assert result.output_or_raise() == "parent-done"
    assert len(seen) == 3
    assert seen[0][0] is seen[2][0] and seen[0][1] is seen[2][1]
    assert seen[1][0] is not seen[0][0] and seen[1][1] is not seen[0][1]
    assert len(client.search_calls) == 2
    assert client.search_calls[0][0] == "delegate now"
    assert json.loads(client.search_calls[1][0]) == {"delegated_task": "inspect", "parent_task": "delegate now"}
    assert len({options["filters"]["run_id"] for _, options in client.search_calls}) == 2
    assert client.entered == 0 and client.exited == 0


@pytest.mark.parametrize("operation", ["add", "update", "delete"])
async def test_public_memory_write_deadline_reports_uncertainty_without_retry(operation):
    import asyncio
    from dataclasses import dataclass

    from a13n_harness.memory import MemoryWriteUnconfirmed
    from pydantic_ai.capabilities import AbstractCapability

    class SlowClient(_FakeMem0Client):
        writes = 0

        async def add(self, *args, **kwargs):
            self.writes += 1
            await asyncio.sleep(60)

        async def update(self, *args, **kwargs):
            self.writes += 1
            await asyncio.sleep(60)

        async def delete(self, *args, **kwargs):
            self.writes += 1
            await asyncio.sleep(60)

    client = SlowClient()
    client.stored = {"id": "memory-1", "memory": "original", "user_id": "user-1"}

    @dataclass
    class WriteMemory(AbstractCapability):
        async def before_model_request(self, ctx, request_context):
            memory = ctx.capabilities.get(MemoryCapability.id)
            assert isinstance(memory, MemoryCapability)
            with pytest.raises(MemoryWriteUnconfirmed):
                if operation == "add":
                    await memory.add(ctx, "text", timeout=0.01)
                elif operation == "update":
                    await memory.update(ctx, "memory-1", "text", timeout=0.01)
                else:
                    await memory.delete(ctx, "memory-1", timeout=0.01)
            return request_context

    async def stream(messages, info):
        yield "done"

    result = (
        await HarnessBuilder()
        .build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=stream),
            capabilities=(
                MemoryCapability(
                    backend=Mem0PlatformBackend(client), scope=MemoryScope.USER, toolset=False, auto_recall=False
                ),
                WriteMemory(),
            ),
        )
        .run("write", bindings=_bindings())
    )
    assert result.output_or_raise() == "done"
    assert client.writes == 1
