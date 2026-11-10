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
async def test_attention_summary_filters_before_limit_and_keeps_explicit_history():
    from types import SimpleNamespace

    from a13n_harness.capabilities.tool_execution import ToolExecutionCapability, tool_execution_info
    from a13n_harness.tool_execution import ToolExecutionRecord

    records = [
        ToolExecutionRecord(
            execution_id=f"old-{index}",
            thread_id="thread-test",
            run_id="run-test",
            tool_call_id=f"call-{index}",
            tool_name="lookup",
            status=status,
            delivery_acknowledged=True,
        )
        for index, status in enumerate(["succeeded", "failed", "cancelled", "lost"] * 26)
    ]
    attention = [
        record.model_copy(update={"execution_id": f"attention-{index}", "delivery_acknowledged": False})
        for index, record in enumerate(records[:4])
    ]
    records.extend(attention)
    records.append(records[0].model_copy(update={"execution_id": "running", "status": "running"}))
    calls = []

    async def summarize(thread_id, *, offset, limit):
        assert thread_id == "thread-test"
        calls.append((offset, limit))
        return tuple(records[offset : offset + limit])

    ctx = SimpleNamespace(
        deps=SimpleNamespace(thread_id="thread-test", tool_execution=SimpleNamespace(summarize=summarize))
    )

    async def handler(request):
        return ModelContextProjection()

    capability = ToolExecutionCapability()
    projection = await capability.wrap_model_context(ctx, INPUT, handler)
    text = projection.blocks[0].content
    assert calls == [(0, 100), (100, 100)]
    assert "old-" not in text
    assert all(record.execution_id in text for record in attention)
    assert '"running"' in text
    explicit = await tool_execution_info(ctx, offset=0, limit=20)
    assert len(explicit) == 20 and explicit[0]["delivery_acknowledged"]
    records.clear()
    empty = await capability.wrap_model_context(ctx, INPUT, handler)
    assert empty.blocks[0].content.endswith(": []")
    assert _drop_unchanged(append([], projection.blocks[0]), empty).blocks


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
