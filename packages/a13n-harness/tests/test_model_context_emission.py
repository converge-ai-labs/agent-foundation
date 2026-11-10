"""Append-on-change is a history observation, not a second durable watermark."""

from dataclasses import replace
from hashlib import sha256

import pytest
from a13n_harness.content import ContentItem, ContentMetadata, input_request, request_input_content
from a13n_harness.errors import DefinitionError
from a13n_harness.model_context import (
    ModelContextBlock,
    ModelContextEmission,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextProjectionRequest,
    ModelContextRequestKind,
    _commit_projection,
    _drop_unchanged,
    _validate_projection,
)
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelRequest, ModelResponse, TextPart

INPUT = ModelContextProjectionRequest(kind=ModelContextRequestKind.INPUT)


def block(text: str, *, key: str | None = None, emission=ModelContextEmission.ON_CHANGE) -> ModelContextBlock:
    return ModelContextBlock(
        source_id="test.snapshot",
        key=key,
        content=text,
        placement=ModelContextPlacement.REQUEST_EPILOGUE,
        emission=emission,
    )


def append(messages: list[ModelMessage], *blocks: ModelContextBlock) -> list[ModelMessage]:
    history = [*messages, input_request("next")]
    projection = ModelContextProjection(blocks=blocks)
    _validate_projection(projection, INPUT)
    return _commit_projection(history, INPUT, _drop_unchanged(history, projection))


def texts(messages: list[ModelMessage]) -> list[str]:
    return [
        item.value
        for message in messages
        if isinstance(message, ModelRequest)
        for item in request_input_content(message)
        if item.metadata.source_id == "test.snapshot" and isinstance(item.value, str)
    ]


def test_unchanged_snapshot_is_not_appended_and_prefix_is_untouched():
    first = append([], block("A"))
    before = ModelMessagesTypeAdapter.dump_json(first)
    second = append([*first, ModelResponse(parts=[TextPart("done")])], block("A"))
    assert texts(second) == ["A"]
    assert ModelMessagesTypeAdapter.dump_json(second[: len(first)]) == before
    assert second[0] is first[0]


def test_latest_comparison_preserves_a_b_a_and_explicit_empty_transitions():
    history = []
    for value in ("A", "A", "B", "A", "No current entries.", "No current entries.", "A"):
        history = append(history, block(value))
    assert texts(history) == ["A", "B", "A", "No current entries.", "A"]


def test_keys_are_independent_and_placement_is_not_an_identity():
    history = append([], block("A", key="global"), block("A", key="project"))
    projection = ModelContextProjection(
        blocks=(replace(block("A", key="global"), placement=ModelContextPlacement.INPUT_PREAMBLE),)
    )
    assert not _drop_unchanged(history, projection).blocks
    history = append(history, block("B", key="project"))
    assert texts(history) == ["A", "A", "B"]


def test_events_and_deltas_keep_owner_triggered_repetition():
    event = block("same event text", emission=ModelContextEmission.ALWAYS)
    assert texts(append(append([], event), event)) == ["same event text", "same event text"]


def test_selected_checkpoint_and_replacement_history_determine_reinjection():
    first = append([], block("A"))
    checkpoint = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(first))
    assert not _drop_unchanged(checkpoint, ModelContextProjection(blocks=(block("A"),))).blocks
    replacement = [input_request("summary")]
    assert texts(append(replacement, block("A"))) == ["A"]
    # Resuming an older checkpoint compares against that history, not later state.
    newer = append(checkpoint, block("B"))
    assert _drop_unchanged(newer, ModelContextProjection(blocks=(block("A"),))).blocks


def test_hash_is_persisted_outside_prompt_text_and_unmarked_history_is_not_a_baseline():
    current = append([], block("A"))
    item = request_input_content(current[-1])[-1]
    assert item.metadata.model_extra["model_context"]["sha256"] == sha256(b"A").hexdigest()
    legacy = input_request([ContentItem("A", ContentMetadata(display=False, source_id="test.snapshot"))])
    before = ModelMessagesTypeAdapter.dump_json([legacy])
    assert _drop_unchanged([legacy], ModelContextProjection(blocks=(block("A"),))).blocks
    assert ModelMessagesTypeAdapter.dump_json([legacy]) == before


def test_projection_validation_precedes_suppression_and_rejects_duplicate_keys():
    history = append([], block("A"))
    invalid = replace(block("A"), placement=ModelContextPlacement.INPUT_PREAMBLE)
    projection = ModelContextProjection(blocks=(invalid,))
    with pytest.raises(DefinitionError, match="INPUT_PREAMBLE"):
        _validate_projection(projection, ModelContextProjectionRequest(kind=ModelContextRequestKind.TOOL_RESULTS))
    assert not _drop_unchanged(history, projection).blocks
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
    assert empty.blocks[0].content.endswith(": []")
    history = append([], active.blocks[0])
    assert _drop_unchanged(history, empty).blocks
    assert not _drop_unchanged(append(history, empty.blocks[0]), empty).blocks
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
        assert empty.blocks[0].content.endswith(": []")
        assert _drop_unchanged(append([], projection.blocks[0]), empty).blocks
    await runtime.close()


def test_context_breakdown_counts_retained_snapshots_not_hashes_or_suppressed_copies():
    from a13n_harness.context_breakdown import measure_context
    from pydantic_ai.models import ModelRequestParameters

    history = []
    for text in ("snapshot A", "snapshot A", "snapshot B", "snapshot B", "snapshot A"):
        snapshot = replace(block(text, key="personal"), context_group="memory", memory_mount="personal")
        history = append(history, snapshot)
    restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(history))
    breakdown = measure_context(restored, ModelRequestParameters())
    expected = len("snapshot A") * 2 + len("snapshot B")
    assert texts(restored) == ["snapshot A", "snapshot B", "snapshot A"]
    assert breakdown.groups["memory"].characters == expected
    assert breakdown.groups["memory"].items["personal"].characters == expected
    assert breakdown.groups["conversation"].characters == 5 * len("next")
    assert breakdown.counted_input_characters == expected + 5 * len("next")
