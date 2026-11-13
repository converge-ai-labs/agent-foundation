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
    assert history.matches_latest(block("B"))
    assert not history.matches_latest(block("A"))
    assert not history.contains(block("C"))
    assert not history.contains(replace(block("A"), source_id="another.source"))


@pytest.mark.parametrize("text", ["", "snapshot", "状态 A\n<file>body</file>"])
def test_latest_comparison_and_metadata_share_the_complete_rendered_utf8_hash(text):
    current = block(text, key="file:AGENTS.md")
    digest = sha256(text.encode("utf-8")).hexdigest()
    assert current.content_hash == digest
    assert current.content_item().metadata.model_extra["model_context"]["sha256"] == digest
    serialized = ModelMessagesTypeAdapter.dump_json(append([], current))
    restored = ModelMessagesTypeAdapter.validate_json(serialized)
    for history in (ModelContextHistory.from_messages(restored), ModelContextHistory.from_blocks((current,))):
        assert history.matches_latest(current)
        assert not history.matches_latest(replace(current, content=text + "\n"))
        assert not history.matches_latest(replace(current, source_id="another.source"))
        assert not history.matches_latest(replace(current, key="file:other.md"))
    assert replace(current, content=text + "\n").content_hash != digest
    assert not ModelContextHistory().matches_latest(current)


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
        selected = () if history.matches_latest(current) else (current,)
        messages = append(messages, *selected)
    assert texts(messages) == ["A", "B", "A", "No current entries.", "A"]


def test_keys_are_independent_and_placement_is_not_identity():
    messages = append([], block("A", key="global"), block("A", key="project"))
    history = ModelContextHistory.from_messages(messages)
    assert history.contains(replace(block("A", key="global"), placement=ModelContextPlacement.INPUT_PREAMBLE))
    assert not history.contains(block("A", key="other"))
    assert history.matches_latest(block("A", key="project"))
    assert not history.matches_latest(block("A", key="other"))
    assert history.matches_latest(replace(block("A", key="global"), placement=ModelContextPlacement.INPUT_PREAMBLE))


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
    assert ModelContextHistory.from_messages(checkpoint).matches_latest(block("A"))
    assert ModelContextHistory.from_messages(newer).matches_latest(block("B"))


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


def test_note_directory_tracks_only_bodies_in_selected_history():
    from a13n_harness.capabilities.working_state import WorkingStateConfiguration, _render_working_state_blocks

    source = "a13n.working-state.notes"
    notes = {f"note-{index}": f"value {index}" for index in range(6)}
    config = WorkingStateConfiguration(tasks_enabled=False)

    def project(messages):
        history = ModelContextHistory.from_messages(messages)
        return _render_working_state_blocks((), {}, notes, config, retained_notes=history.retained_texts(source))

    (first,) = project([])
    assert first.content.count("<note key=") == 6
    baseline = append([], first)
    assert not project(baseline)
    notes["note-2"] = 'new <note key="fake"> & body'
    (second,) = project(baseline)
    assert second.content.count("<note key=") == 1
    assert second.content.count("<note-key ") == 5
    assert "&lt;note key=&quot;fake&quot;&gt; &amp; body" in second.content
    history = append(baseline, second)
    assert not project(history)
    del notes["note-5"]
    (deleted,) = project(history)
    assert '<note-deleted key="note-5" />' in deleted.content
    assert deleted.content.count("<note-key ") == 5
    history = append(history, deleted)
    assert not project(history)
    notes["note-5"] = "value 5"
    (restored,) = project(history)
    assert '<note key="note-5">value 5</note>' in restored.content

    # A retained directory with no matching baseline cannot establish five unchanged bodies.
    (repaired,) = project(append([], second))
    assert repaired.content.count("<note key=") == 5
    assert '<note-key key="note-2" />' in repaired.content
    assert project([])[0].content.count("<note key=") == 6
    assert project(baseline)[0] == second
    # User-authored text and unordered Live delivery cannot establish a baseline.
    assert project([input_request(first.content)]) == project([])
    assert (
        ModelContextHistory.from_blocks((second,), unordered=frozenset({(source, None)})).retained_texts(source) == ()
    )


def test_note_refs_and_bulk_deletions_stay_bounded_and_recover_missing_bodies():
    from a13n_harness.capabilities.working_state import _render_notes

    notes = {f"key-{n:02d}-🎵": "x" * 100 for n in range(12)}
    first = _render_notes(notes, 12, 512, ())
    assert first and "<note-ref" in first
    assert len(first.encode()) <= 512
    # A reference never establishes a full value. With room it must upgrade.
    second = _render_notes(notes, 12, 8192, (first,))
    assert second and second.count("<note key=") + first.count("<note key=") == len(notes)
    deleted = _render_notes({}, 2, 512, (first, second))
    assert deleted and deleted.count("<note-deleted ") == 2
    assert '<deletions-omitted count="10" />' in deleted
    assert _render_notes({}, 2, 512, (first, second, deleted)) is None


def test_attention_row_is_single_line_and_preserves_actionable_states():
    from a13n_harness.capabilities.tool_execution import _attention_line
    from a13n_harness.tool_execution import ToolExecutionRecord

    record = ToolExecutionRecord(
        execution_id="exec-1",
        thread_id="thread",
        run_id="run",
        tool_call_id="call",
        tool_name="shell_exec",
        label="Preview\nserver|\u202e",
        status="running",
        cancel_requested=True,
    )
    assert _attention_line(record) == "- exec-1 | Preview server | running, cancellation requested"
    failed = record.model_copy(update={"label": None, "status": "failed", "result_available": True})
    assert _attention_line(failed) == "- exec-1 | shell_exec | failed, result available"


@pytest.mark.anyio
async def test_attention_summary_reads_one_priority_page_and_keeps_explicit_history(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from a13n_harness._tool_execution import RunToolExecutions
    from a13n_harness.capabilities.tool_execution import (
        ToolExecutionCapability,
        tool_execution_info,
        tool_execution_result,
    )
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
        operation,
        thread_id="thread-test",
        run_id="run-test",
        tool_call_id="unread",
        tool_name="lookup",
        label="Check docs",
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
    assert f"- {unread.execution_id} | Check docs | finished, result available" in text
    assert f"- {queued.execution_id} | lookup | queued" in text
    assert not (await runtime.info("thread-test", unread.execution_id)).delivery_acknowledged
    explicit = await tool_execution_info(ctx, offset=2, limit=100)
    assert len(explicit) == 100 and all(record["execution_id"].startswith("old-") for record in explicit)
    ctx.tool_call_id = "retrieve-docs"

    ctx.deps._tool_executions = RunToolExecutions()
    result_id = next(line.split(" | ")[0][2:] for line in text.splitlines() if " | Check docs | " in line)
    assert await tool_execution_result(ctx, result_id) == "unread"
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
    assert empty.blocks[0].content.splitlines()[0].endswith("(0)")
    assert "Finished executions without retained results are no longer listed" in empty.blocks[0].content
    assert "tool_execution_info" in empty.blocks[0].content
    assert "absence does not establish success" in empty.blocks[0].content
    history = append([], active.blocks[0])
    assert not ModelContextHistory.from_messages(history).matches_latest(empty.blocks[0])
    committed = append(history, empty.blocks[0])
    request = replace(INPUT, history=ModelContextHistory.from_messages(committed))
    assert not (await capability.wrap_model_context(ctx, request, handler)).blocks
    assert not (await runtime.info("thread-test", record.execution_id)).delivery_acknowledged
    await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("count", [0, 20, 21])
async def test_attention_summary_bounds_results_and_signals_overflow(count, monkeypatch):
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
    records = [line for line in text.splitlines() if line.startswith("- ")]
    assert len(records) == min(count, 20)
    assert ("More executions require attention" in text) == (count > 20)
    assert all(line.endswith(" | lookup | finished, result available") for line in records)
    if count:
        stored = await runtime.summarize("thread-test", limit=count)
        assert len(stored) == count
        assert all(not record.delivery_acknowledged for record in stored)
        for record in stored:
            await runtime.acknowledge_delivery("thread-test", record.execution_id)
        empty = await capability.wrap_model_context(ctx, INPUT, handler)
        assert empty.blocks[0].content.splitlines()[0].endswith("(0)")
        assert not ModelContextHistory.from_messages(append([], projection.blocks[0])).contains(empty.blocks[0])
    await runtime.close()


def test_context_breakdown_counts_retained_snapshots_not_hashes_or_suppressed_copies():
    from a13n_harness.context_breakdown import measure_context
    from pydantic_ai.models import ModelRequestParameters

    history = []
    for text in ("snapshot A", "snapshot A", "snapshot B", "snapshot B", "snapshot A"):
        snapshot = replace(block(text, key="personal"), context_group="memory", memory_mount="personal")
        retained = ModelContextHistory.from_messages(history)
        history = append(history, *((snapshot,) if not retained.matches_latest(snapshot) else ()))
    restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(history))
    breakdown = measure_context(restored, ModelRequestParameters())
    expected = len("snapshot A") * 2 + len("snapshot B")
    assert texts(restored) == ["snapshot A", "snapshot B", "snapshot A"]
    assert breakdown.groups["memory"].characters == expected
    assert breakdown.groups["memory"].items["personal"].characters == expected
    assert breakdown.groups["conversation"].characters == 5 * len("next")
    assert breakdown.counted_input_characters == expected + 5 * len("next")


@pytest.mark.anyio
async def test_independent_file_snapshots_compare_latest_and_reemit_restored_bodies():
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
    assert [item.key for item in emitted[2]] == ["file:AGENTS.md"]
    assert [item.key for item in emitted[3]] == ["file:AGENTS.md"]
    assert '<file path="AGENTS.md">\nB\n</file>' in emitted[2][0].content
    assert emitted[3][0] == emitted[0][1]
    assert "sha256" not in emitted[0][0].content
    # Compaction may retain the selection while discarding an independent body.
    retained_selection = append([], emitted[0][0], emitted[0][2], emitted[0][3])
    request = replace(INPUT, history=ModelContextHistory.from_messages(retained_selection))
    missing = await capability.wrap_model_context(None, request, handler)
    assert [item.key for item in missing.blocks] == ["file:AGENTS.md"]
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
            return ModelContextProjection(blocks=() if request.history.matches_latest(current) else (current,))

    class Outer:
        async def wrap_model_context(self, ctx, request, handler):
            inner = await handler(request)
            rendered = tuple(replace(item, content="final:" + item.content) for item in inner.blocks)
            return ModelContextProjection(
                blocks=tuple(item for item in rendered if not request.history.matches_latest(item))
            )

    deps = Source()
    deps.model_context = Outer()
    ctx = SimpleNamespace(deps=deps, capabilities={})
    first = await _project_model_context(ctx, INPUT)
    assert [item.content for item in first.blocks] == ["final:inner"]
    request = replace(INPUT, history=ModelContextHistory.from_messages(append([], *first.blocks)))
    assert not request.history.contains(block("inner"))
    assert request.history.contains(block("final:inner"))
    assert not (await _project_model_context(ctx, request)).blocks
