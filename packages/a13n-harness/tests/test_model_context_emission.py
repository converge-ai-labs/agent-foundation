"""Retained context facts and source-owned injection decisions."""

from dataclasses import FrozenInstanceError, replace
from hashlib import sha256

import pytest
from a13n_harness.content import ContentItem, ContentMetadata, input_request, request_input_content
from a13n_harness.errors import DefinitionError
from a13n_harness.model_context import (
    ModelContextBlock,
    ModelContextHistory,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextProjectionRequest,
    ModelContextRequestKind,
    _commit_projection,
    _validate_projection,
)
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelRequest, ModelResponse, TextPart

INPUT = ModelContextProjectionRequest(kind=ModelContextRequestKind.INPUT)


def block(text: str, *, key: str | None = None) -> ModelContextBlock:
    return ModelContextBlock(
        source_id="test.snapshot",
        key=key,
        content=text,
        placement=ModelContextPlacement.REQUEST_EPILOGUE,
    )


def append(messages: list[ModelMessage], *blocks: ModelContextBlock) -> list[ModelMessage]:
    history = [*messages, input_request("next")]
    projection = ModelContextProjection(blocks=blocks)
    _validate_projection(projection, INPUT)
    return _commit_projection(history, INPUT, projection)


def texts(messages: list[ModelMessage]) -> list[str]:
    return [
        item.value
        for message in messages
        if isinstance(message, ModelRequest)
        for item in request_input_content(message)
        if item.metadata.source_id == "test.snapshot" and isinstance(item.value, str)
    ]


def test_contains_observes_any_retained_occurrence_not_just_latest():
    messages = append(append([], block("A")), block("B"))
    history = ModelContextHistory.from_messages(messages)
    assert history.contains(block("A")) and history.contains(block("B"))
    assert history.latest("test.snapshot") == "B"
    assert not history.contains(block("C"))
    assert not history.contains(replace(block("A"), source_id="another.source"))


def test_commit_keeps_every_returned_block_even_if_already_retained():
    first = append([], block("A"))
    before = ModelMessagesTypeAdapter.dump_json(first)
    second = append([*first, ModelResponse(parts=[TextPart("done")])], block("A"))
    assert texts(second) == ["A", "A"]
    assert ModelMessagesTypeAdapter.dump_json(second[: len(first)]) == before
    assert second[0] is first[0]


def test_state_owner_can_describe_a_b_a_and_explicit_empty_transitions():
    messages = []
    for value in ("A", "A", "B", "A", "No current entries.", "No current entries.", "A"):
        current = block(value)
        history = ModelContextHistory.from_messages(messages)
        selected = () if history.latest(current.source_id, current.key) == current.content else (current,)
        messages = append(messages, *selected)
    assert texts(messages) == ["A", "B", "A", "No current entries.", "A"]


def test_keys_are_independent_and_placement_is_not_identity():
    messages = append([], block("A", key="global"), block("A", key="project"))
    history = ModelContextHistory.from_messages(messages)
    assert history.contains(replace(block("A", key="global"), placement=ModelContextPlacement.INPUT_PREAMBLE))
    assert not history.contains(block("A", key="other"))
    assert history.latest("test.snapshot", "project") == "A"


def test_events_and_deltas_keep_owner_triggered_repetition():
    event = block("same event text")
    assert texts(append(append([], event), event)) == ["same event text", "same event text"]


def test_selected_checkpoint_and_replacement_history_determine_presence():
    first = append([], block("A"))
    checkpoint = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(first))
    assert ModelContextHistory.from_messages(checkpoint).contains(block("A"))
    assert not ModelContextHistory.from_messages([input_request("summary")]).contains(block("A"))
    newer = append(checkpoint, block("B"))
    assert ModelContextHistory.from_messages(newer).contains(block("A"))
    assert ModelContextHistory.from_messages(checkpoint).latest("test.snapshot") == "A"
    assert ModelContextHistory.from_messages(newer).latest("test.snapshot") == "B"


def test_canonical_metadata_roundtrips_and_stale_or_unmarked_text_does_not_pin():
    current = append([], block("A"))
    item = request_input_content(current[-1])[-1]
    assert item.metadata.model_extra["model_context"] == {
        "key": None,
        "placement": "request_epilogue",
        "sha256": sha256(b"A").hexdigest(),
    }
    legacy = input_request([ContentItem("A", ContentMetadata(display=False, source_id="test.snapshot"))])
    stale = input_request([ContentItem("rewritten", item.metadata)])
    assert not ModelContextHistory.from_messages([legacy, stale, input_request("A")]).contains(block("A"))
    assert not ModelContextHistory.from_messages([stale]).contains(block("rewritten"))
    restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(current))
    assert ModelContextHistory.from_messages(restored).contains(block("A"))


def test_history_is_detached_readonly_and_query_does_not_reserve_delivery():
    messages = append([], block("A"))
    history = ModelContextHistory.from_messages(messages)
    messages.clear()
    assert history.contains(block("A"))
    assert not history.contains(block("B"))
    assert not history.contains(block("B"))
    with pytest.raises(FrozenInstanceError):
        history._entries = ()


def test_projection_validation_rejects_unsafe_placement_and_duplicate_keys():
    invalid = replace(block("A"), placement=ModelContextPlacement.INPUT_PREAMBLE)
    with pytest.raises(DefinitionError, match="INPUT_PREAMBLE"):
        _validate_projection(
            ModelContextProjection(blocks=(invalid,)),
            ModelContextProjectionRequest(kind=ModelContextRequestKind.TOOL_RESULTS),
        )
    with pytest.raises(DefinitionError, match="unique"):
        _validate_projection(ModelContextProjection(blocks=(block("A"), block("B"))), INPUT)


@pytest.mark.anyio
async def test_attention_summary_reads_one_priority_page_and_keeps_explicit_history(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from a13n_harness.capabilities.tool_execution import ToolExecutionCapability, tool_execution_info
    from a13n_harness.tool_execution import (
        MemoryToolExecutionStore,
        StoredToolExecution,
        ToolExecutionRecord,
        ToolExecutionRuntime,
    )

    store = MemoryToolExecutionStore(max_completed=400)
    for index in range(301):
        status = ("succeeded", "failed", "cancelled", "lost")[index % 4]
        record = ToolExecutionRecord(
            execution_id=f"old-{index}",
            thread_id="thread-test",
            run_id="run-test",
            tool_call_id=f"call-{index}",
            tool_name="lookup",
            status=status,
            result_available=status == "succeeded",
            delivery_acknowledged=status == "succeeded",
        )
        await store.save(StoredToolExecution(record, "delivered" if record.result_available else None))
    runtime = ToolExecutionRuntime(store=store)

    async def operation():
        return "unread"

    unread = runtime.admit(
        operation, thread_id="thread-test", run_id="run-test", tool_call_id="unread", tool_name="lookup"
    )
    await runtime.join()
    queued = runtime.admit(
        operation, thread_id="thread-test", run_id="run-test", tool_call_id="queued", tool_name="lookup"
    )
    listed = AsyncMock(wraps=store.list)
    monkeypatch.setattr(store, "list", listed)
    ctx = SimpleNamespace(deps=SimpleNamespace(thread_id="thread-test", tool_execution=runtime))

    async def handler(request):
        return ModelContextProjection()

    projection = await ToolExecutionCapability().wrap_model_context(ctx, INPUT, handler)
    listed.assert_awaited_once_with("thread-test", offset=0, limit=21)
    text = projection.blocks[0].content
    assert "old-" not in text
    assert unread.execution_id in text and queued.execution_id in text
    assert '"queued"' in text
    assert not (await runtime.info("thread-test", unread.execution_id)).delivery_acknowledged
    explicit = await tool_execution_info(ctx, offset=2, limit=100)
    assert len(explicit) == 100 and all(record["execution_id"].startswith("old-") for record in explicit)
    await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["failed", "cancelled", "lost"])
async def test_attention_summary_clears_real_terminal_executions_without_results(status, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from a13n_harness.capabilities.tool_execution import (
        ToolExecutionCapability,
        tool_execution_info,
        tool_execution_result,
    )
    from a13n_harness.tool_execution import ToolExecutionRuntime

    runtime = ToolExecutionRuntime()
    if status == "lost":
        save = runtime.store.save

        async def fail_terminal(execution):
            if execution.record.terminal:
                raise OSError("result publication failed")
            await save(execution)

        monkeypatch.setattr(runtime.store, "save", fail_terminal)

    async def operation():
        if status == "failed":
            raise RuntimeError("operation failed")
        if status == "cancelled":
            raise asyncio.CancelledError
        return "result that cannot be published"

    record = runtime.admit(
        operation, thread_id="thread-test", run_id="run-test", tool_call_id="call-test", tool_name="lookup"
    )
    ctx = SimpleNamespace(deps=SimpleNamespace(thread_id="thread-test", tool_execution=runtime))

    async def handler(request):
        return ModelContextProjection()

    capability = ToolExecutionCapability()
    active = await capability.wrap_model_context(ctx, INPUT, handler)
    assert record.execution_id in active.blocks[0].content
    await runtime.join()
    terminal = await tool_execution_info(ctx, record.execution_id)
    assert terminal["status"] == status
    assert not terminal["result_available"] and not terminal["delivery_acknowledged"]
    assert (await tool_execution_result(ctx, record.execution_id))["status"] == status
    empty = await capability.wrap_model_context(ctx, INPUT, handler)
    assert empty.blocks[0].content.splitlines()[0].endswith(": []")
    assert "Finished executions without retained results are no longer listed" in empty.blocks[0].content
    assert "tool_execution_info" in empty.blocks[0].content
    assert "absence does not establish success" in empty.blocks[0].content
    history = append([], active.blocks[0])
    assert ModelContextHistory.from_messages(history).latest(empty.blocks[0].source_id) != empty.blocks[0].content
    committed = append(history, empty.blocks[0])
    request = replace(INPUT, history=ModelContextHistory.from_messages(committed))
    assert not (await capability.wrap_model_context(ctx, request, handler)).blocks
    assert not (await runtime.info("thread-test", record.execution_id)).delivery_acknowledged
    await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("count", [0, 20, 21])
async def test_attention_summary_bounds_results_and_signals_overflow(count, monkeypatch):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from a13n_harness.capabilities.tool_execution import ToolExecutionCapability
    from a13n_harness.tool_execution import ToolExecutionRuntime

    runtime = ToolExecutionRuntime()

    async def operation():
        return "unread"

    for index in range(count):
        runtime.admit(
            operation, thread_id="thread-test", run_id="run-test", tool_call_id=f"call-{index}", tool_name="lookup"
        )
        await runtime.join()
    listed = AsyncMock(wraps=runtime.store.list)
    monkeypatch.setattr(runtime.store, "list", listed)
    ctx = SimpleNamespace(deps=SimpleNamespace(thread_id="thread-test", tool_execution=runtime))

    async def handler(request):
        return ModelContextProjection()

    capability = ToolExecutionCapability()
    projection = await capability.wrap_model_context(ctx, INPUT, handler)
    listed.assert_awaited_once_with("thread-test", offset=0, limit=21)
    text = projection.blocks[0].content
    records = json.loads(text.split(": ", 1)[1].split("\n", 1)[0])
    assert len(records) == min(count, 20)
    assert ("More executions require attention" in text) == (count > 20)
    assert all(not record["delivery_acknowledged"] for record in records)
    if count:
        stored = await runtime.summarize("thread-test", limit=count)
        assert len(stored) == count
        assert all(not record.delivery_acknowledged for record in stored)
        for record in stored:
            await runtime.acknowledge_delivery("thread-test", record.execution_id)
        empty = await capability.wrap_model_context(ctx, INPUT, handler)
        assert empty.blocks[0].content.splitlines()[0].endswith(": []")
        assert not ModelContextHistory.from_messages(append([], projection.blocks[0])).contains(empty.blocks[0])
    await runtime.close()


def test_context_breakdown_counts_retained_snapshots_not_hashes_or_suppressed_copies():
    from a13n_harness.context_breakdown import measure_context
    from pydantic_ai.models import ModelRequestParameters

    history = []
    for text in ("snapshot A", "snapshot A", "snapshot B", "snapshot B", "snapshot A"):
        snapshot = replace(block(text, key="personal"), context_group="memory", memory_mount="personal")
        retained = ModelContextHistory.from_messages(history)
        history = append(history, *((snapshot,) if retained.latest(snapshot.source_id, snapshot.key) != text else ()))
    restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(history))
    breakdown = measure_context(restored, ModelRequestParameters())
    expected = len("snapshot A") * 2 + len("snapshot B")
    assert texts(restored) == ["snapshot A", "snapshot B", "snapshot A"]
    assert breakdown.groups["memory"].characters == expected
    assert breakdown.groups["memory"].items["personal"].characters == expected
    assert breakdown.groups["conversation"].characters == 5 * len("next")
    assert breakdown.counted_input_characters == expected + 5 * len("next")


@pytest.mark.anyio
async def test_independent_file_bodies_are_reused_while_selection_conveys_restoration():
    from a13n_harness.capabilities.context import FileContextConfiguration, _FileContextRunCapability

    async def handler(request):
        return ModelContextProjection()

    configuration = FileContextConfiguration()
    history = []
    emitted = []
    for body in ("A", "A", "B", "A"):
        capability = _FileContextRunCapability(configuration, (("AGENTS.md", body), ("guide.md", "fixed")), None)
        request = replace(INPUT, history=ModelContextHistory.from_messages(history))
        projection = await capability.wrap_model_context(None, request, handler)
        emitted.append(projection.blocks)
        history = append(history, *projection.blocks)
    assert [item.key for item in emitted[0]] == ["files", "file:AGENTS.md", "file:guide.md", "project-guidance"]
    assert not emitted[1]
    assert [item.key for item in emitted[2]] == ["files", "file:AGENTS.md"]
    assert [item.key for item in emitted[3]] == ["files"]
    assert sha256(b"A").hexdigest() in emitted[3][0].content
    # A new history window needs missing bodies even when selection is identical.
    replacement = _FileContextRunCapability(configuration, (("AGENTS.md", "A"),), None)
    fresh = await replacement.wrap_model_context(None, INPUT, handler)
    assert "file:AGENTS.md" in {item.key for item in fresh.blocks}
    cleared = _FileContextRunCapability(configuration, (), None)
    projection = await cleared.wrap_model_context(
        None, replace(INPUT, history=ModelContextHistory.from_messages(history)), handler
    )
    assert [item.key for item in projection.blocks] == ["files"]
    assert "No file context is loaded" in projection.blocks[0].content


@pytest.mark.anyio
async def test_final_outer_transform_owns_decision_against_committed_rendering():
    from types import SimpleNamespace

    from a13n_harness.model_context import _project_model_context

    class Source:
        async def project_model_context(self, request):
            current = block("inner")
            return ModelContextProjection(blocks=() if request.history.contains(current) else (current,))

    class Outer:
        async def wrap_model_context(self, ctx, request, handler):
            inner = await handler(request)
            rendered = tuple(replace(item, content="final:" + item.content) for item in inner.blocks)
            return ModelContextProjection(blocks=tuple(item for item in rendered if not request.history.contains(item)))

    deps = Source()
    deps.model_context = Outer()
    ctx = SimpleNamespace(deps=deps, capabilities={})
    first = await _project_model_context(ctx, INPUT)
    assert [item.content for item in first.blocks] == ["final:inner"]
    request = replace(INPUT, history=ModelContextHistory.from_messages(append([], *first.blocks)))
    assert not request.history.contains(block("inner"))
    assert request.history.contains(block("final:inner"))
    assert not (await _project_model_context(ctx, request)).blocks
