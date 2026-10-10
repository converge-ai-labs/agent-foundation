from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from a13n_harness import HarnessBuilder, HarnessState, RunBindings
from a13n_harness.capabilities import CompactionCapability, CompactionPolicy, HandoffCapability
from a13n_harness.capabilities.context import _COMPACTION_PROMPT
from a13n_harness.model_context import user_prompt_content
from a13n_harness_ui.display_history import (
    DisplayHistory,
    DisplayHistoryCollector,
    detach_display_history,
    import_display_history,
    saved_display_history,
    with_display_history,
)
from a13n_harness_ui.display_projection import display_entries
from a13n_harness_ui.root_checkpoint import RootCheckpointCapability
from a13n_harness_ui.thread_projection import _message_entry
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.usage import RequestUsage

pytestmark = pytest.mark.anyio


def visible(history: DisplayHistory) -> list[str]:
    return [
        part.text
        for entry in display_entries(history)
        for part in entry.parts
        if part.text and part.metadata.display is not False and part.kind != "system"
    ]


def test_removing_runtime_display_preserves_every_other_namespace_and_the_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from a13n_harness.state import AgentContextStateSnapshot, CapabilityState

    native = HarnessState.new(
        message_history=[ModelRequest(parts=[UserPromptPart("Native context")])],
        agent_context_state=AgentContextStateSnapshot(
            entries={
                "unknown.capability": CapabilityState(version="future", data={"nested": [1, {"keep": True}]}),
                "a13n.harness-ui.other": CapabilityState(version="7", data=["retain this UI namespace"]),
            }
        ),
    )
    display = DisplayHistoryCollector(native.message_history).capture(native.message_history)
    saved = with_display_history(native, display)
    original = saved.model_dump_json()
    reads = 0
    getter = AgentContextStateSnapshot.entries.fget

    def counted(snapshot):
        nonlocal reads
        if snapshot is saved.agent_context_state:
            reads += 1
        return getter(snapshot)

    monkeypatch.setattr(AgentContextStateSnapshot, "entries", property(counted))
    runtime, restored = detach_display_history(saved)
    assert restored == display
    assert reads == 1
    assert runtime == native
    assert runtime.message_history_json is saved.message_history_json
    assert runtime.environment_states_json is saved.environment_states_json
    assert saved.model_dump_json() == original
    unchanged, absent = detach_display_history(runtime)
    assert unchanged is runtime and absent is None
    assert with_display_history(runtime, restored) == saved


@pytest.mark.parametrize("kind", ["handoff", "compaction"])
async def test_display_history_survives_repeated_context_replacement_and_reload(kind: str) -> None:
    previous = HarnessState.new(
        message_history=[
            ModelRequest(parts=[UserPromptPart("Original user request")]),
            ModelResponse(parts=[TextPart("Original answer")], usage=RequestUsage(input_tokens=1000)),
        ]
    )
    display: DisplayHistory | None = None
    for round_number in range(2):
        collector = DisplayHistoryCollector(previous.message_history, display)
        snapshots: list[DisplayHistory] = []
        requests = 0

        async def save(state: HarnessState, *, snapshots=snapshots, collector=collector) -> str:
            snapshots.append(collector.capture(state.message_history))
            return f"checkpoint-{len(snapshots)}"

        async def model(
            messages: list[ModelMessage], info: AgentInfo, *, round_number=round_number
        ) -> AsyncIterator[str | DeltaToolCalls]:
            nonlocal requests
            if any(
                _COMPACTION_PROMPT in str(item.content)
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart)
                for item in user_prompt_content(part)
            ):
                yield "# Summary\n\n**Keep the decisions**"
                return
            requests += 1
            if kind == "handoff" and requests == 1:
                yield {
                    0: DeltaToolCall(
                        name="summarize",
                        json_args=json.dumps({"content": "**Keep the decisions**"}),
                        tool_call_id=f"summary-{round_number}",
                    )
                }
            else:
                yield f"Answer {round_number}"

        executable = HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=(
                RootCheckpointCapability(save),
                HandoffCapability(),
                *((CompactionCapability(CompactionPolicy(trigger_tokens=1)),) if kind == "compaction" else ()),
            ),
        )
        async with executable.stream(
            f"Request {round_number}",
            bindings=RunBindings.embedded(producer_observer=collector.observe),
            previous_state=previous,
        ) as run:
            async for _ in run:
                pass
            result = run.result
        assert result is not None and result.state is not None
        result.output_or_raise()
        assert snapshots
        display = collector.capture(result.state.message_history)
        text = visible(display)
        assert text.count("Original user request") == 1
        assert text.count("Original answer") == 1
        for number in range(round_number + 1):
            assert text.count(f"Request {number}") == 1
            assert text.count(f"Answer {number}") == 1
        summaries = [item for item in display.items if item.content.get("name") == f"a13n.context.{kind}_summary"]
        assert len(summaries) == round_number + 1
        assert len({item.content["value"]["event"]["operation_id"] for item in summaries}) == len(summaries)
        assert not any(_COMPACTION_PROMPT in item for item in text)
        assert not any(
            isinstance(message, ModelResponse)
            and any(isinstance(part, TextPart) and part.content == "Original answer" for part in message.parts)
            for message in result.state.message_history
        )
        # Reopen exactly the serialized display and native state; display never
        # becomes the model's execution history.
        stored = with_display_history(result.state, display)
        assert stored.message_history == result.state.message_history
        previous = HarnessState.model_validate_json(stored.model_dump_json())
        display = saved_display_history(previous)
        assert display is not None


async def test_repeated_identical_messages_keep_distinct_positions_and_detached_snapshots() -> None:
    message = ModelRequest(parts=[UserPromptPart("Same input")])
    collector = DisplayHistoryCollector([message, message])
    snapshot = collector.capture()
    message.parts = [UserPromptPart("Updated input")]
    assert visible(snapshot) == ["Same input", "Same input"]
    messages = [message, message, ModelResponse(parts=[TextPart("Answer")])]
    updated = import_display_history(messages)
    assert visible(updated) == ["Updated input", "Updated input", "Answer"]
    assert visible(snapshot) == ["Same input", "Same input"]
    native = HarnessState.new(message_history=messages)
    stored = with_display_history(native, updated)
    assert saved_display_history(stored) == updated
    # Compact presentation is independent of model-context replacement.
    advanced = HarnessState.new(
        message_history=[*native.message_history, ModelRequest(parts=[UserPromptPart("New")])],
        agent_context_state=stored.agent_context_state,
    )
    assert saved_display_history(advanced) == updated


async def test_restore_decodes_saved_messages_once_and_keeps_copies_detached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from a13n_harness_ui import display_history

    history = [ModelRequest(parts=[UserPromptPart("Input")]), ModelResponse(parts=[TextPart("Answer")])]
    history[-1].metadata = {"a13n.harness-ui.completed": True}
    saved = import_display_history(history)
    decode = display_history.decode_messages
    calls = 0

    def counted_decode(value: bytes) -> tuple[ModelMessage, ...]:
        nonlocal calls
        calls += 1
        return decode(value)

    monkeypatch.setattr(display_history, "decode_messages", counted_decode)
    collector = DisplayHistoryCollector(history, saved)
    assert calls == 0
    history[-1].parts = [TextPart("Changed answer")]
    updated = collector.capture(history)
    assert visible(updated) == ["Input", "Answer"]
    assert updated.completed == ("import:1",)
    assert visible(saved) == ["Input", "Answer"]
    assert saved.completed == ("import:1",)


@pytest.mark.parametrize("kind", ["plain", "handoff", "compaction"])
async def test_collection_hooks_do_not_serialize_unused_snapshots(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from a13n_harness_ui import display_history

    collector = DisplayHistoryCollector([])
    encode = display_history.DisplayHistory.model_dump
    encoded = 0

    def counted_encode(messages: object) -> bytes:
        nonlocal encoded
        encoded += 1
        return encode(messages)

    monkeypatch.setattr(display_history.DisplayHistory, "model_dump", counted_encode)
    requests = 0

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests
        requests += 1
        if kind == "handoff" and requests == 1:
            yield {0: DeltaToolCall(name="summarize", json_args='{"content":"Keep input"}', tool_call_id="s")}
        else:
            yield "Answer"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=[
            HandoffCapability(),
            *([CompactionCapability(CompactionPolicy(trigger_tokens=1))] if kind == "compaction" else []),
        ],
    )
    previous = HarnessState.new(
        message_history=[
            ModelRequest(parts=[UserPromptPart("Original")]),
            ModelResponse(parts=[TextPart("Old answer")], usage=RequestUsage(input_tokens=1000)),
        ]
    )
    result = await executable.run(
        "Input", bindings=RunBindings.embedded(producer_observer=collector.observe), previous_state=previous
    )
    result.output_or_raise()
    assert result.state is not None
    assert encoded == 0
    snapshot = collector.capture(result.state.message_history, completed=True)
    assert encoded == 0  # Serialization belongs only to checkpoint publication.
    assert "Input" in visible(snapshot)
    assert visible(snapshot)[-1] == "Answer"
    assert snapshot.completed


@pytest.mark.parametrize("checkpoint", [False, True])
@pytest.mark.parametrize("adjacent_requests", [False, True])
async def test_suspended_response_replaces_its_display_slot_on_resume(
    checkpoint: bool, adjacent_requests: bool
) -> None:
    previous = HarnessState.new(
        message_history=[
            *([ModelRequest(parts=[UserPromptPart("First")], run_id="old")] if adjacent_requests else []),
            ModelRequest(parts=[UserPromptPart("Continue")], run_id="old"),
            ModelResponse(parts=[TextPart("partial ")], state="suspended", run_id="old"),
        ]
    )
    collector = DisplayHistoryCollector(previous.message_history)
    snapshots: list[tuple[HarnessState, DisplayHistory]] = []

    async def save(state: HarnessState) -> str:
        snapshots.append((state, collector.capture(state.message_history)))
        return "saved"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "finished"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=[*([RootCheckpointCapability(save)] if checkpoint else [])],
    )
    result = await executable.run(
        None, bindings=RunBindings.embedded(producer_observer=collector.observe), previous_state=previous
    )
    result.output_or_raise()
    assert result.state is not None
    final = collector.capture(result.state.message_history)
    inputs = ["First", "Continue"] if adjacent_requests else ["Continue"]
    assert visible(final) == [*inputs, "partial ", "finished"]
    assert len(display_entries(final)) == len(inputs) + 1
    assert final.pending_response is None
    if checkpoint:
        state, saved = snapshots[0]
        assert visible(saved) == [*inputs, "partial "]
        assert saved.pending_response == f"import:{len(inputs)}"
        # A crash after the provider-boundary checkpoint must not strand the
        # partial response in display history alongside the retried response.
        reopened = DisplayHistoryCollector(
            state.message_history, DisplayHistory.model_validate_json(saved.model_dump_json())
        )
        retry = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=model))
        retried = await retry.run(
            None, bindings=RunBindings.embedded(producer_observer=reopened.observe), previous_state=state
        )
        retried.output_or_raise()
        assert retried.state is not None
        assert visible(reopened.capture(retried.state.message_history)) == [*inputs, "finished"]


@pytest.mark.parametrize("kind", ["handoff", "compaction"])
async def test_context_summary_projection_keeps_complete_markdown(kind: str) -> None:
    from pydantic_ai.messages import TextContent

    content = "# Decisions\n\n" + "**Keep this complete.**\n\n" * 4000
    metadata = {"a13n.context": kind, "operation_id": "summary-long"}
    message = (
        ModelRequest(parts=[UserPromptPart([TextContent(content, metadata=metadata)])], metadata=metadata)
        if kind == "handoff"
        else ModelResponse(parts=[TextPart(content)], metadata={**metadata, "keep": "compact"})
    )
    entry = _message_entry(0, message)
    assert entry.parts[0].text == content
    assert type(entry).model_validate_json(entry.model_dump_json()) == entry


@pytest.mark.parametrize("history_kind", ["handoff", "two_requests", "three_requests"])
async def test_preparation_failure_preserves_saved_display_after_native_request_merging(history_kind: str) -> None:
    from pydantic_ai.capabilities import AbstractCapability

    collector = DisplayHistoryCollector([])
    calls = 0

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {
                0: DeltaToolCall(
                    name="summarize", json_args=json.dumps({"content": "Keep the decision"}), tool_call_id="s"
                )
            }
        else:
            yield "Saved answer"

    async def save(state: HarnessState) -> str:
        collector.capture(state.message_history)
        return "saved"

    if history_kind == "handoff":
        executable = HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=[RootCheckpointCapability(save), HandoffCapability()],
        )
        result = await executable.run(
            "Original input", bindings=RunBindings.embedded(producer_observer=collector.observe)
        )
        assert result.output_or_raise() == "Saved answer" and result.state is not None
        state = result.state
    else:
        count = 2 if history_kind == "two_requests" else 3
        state = HarnessState.new(
            message_history=[
                *[ModelRequest(parts=[UserPromptPart(f"Old input {number}")]) for number in range(count)],
                ModelResponse(parts=[TextPart("Saved answer")]),
            ]
        )
    if history_kind != "handoff":
        collector = DisplayHistoryCollector(state.message_history)
    display = collector.capture(completed=True)
    previous = HarnessState.model_validate_json(with_display_history(state, display).model_dump_json())
    reopened = DisplayHistoryCollector(previous.message_history, saved_display_history(previous))

    class FailInstructions(AbstractCapability):
        def get_instructions(self):
            async def instructions(ctx):
                raise RuntimeError("instruction backend unavailable")

            return instructions

    async def unexpected_model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        pytest.fail("Preparation must fail before model dispatch")
        yield "unreachable"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=unexpected_model),
        capabilities=[FailInstructions()],
    )
    failed = await executable.run(
        "New input", bindings=RunBindings.embedded(producer_observer=reopened.observe), previous_state=previous
    )
    assert failed.status == "failed" and failed.state is not None
    updated = reopened.capture(failed.state.message_history)
    assert visible(updated) == [*visible(display), "New input"]
    assert updated.completed == display.completed
    saved = HarnessState.model_validate_json(with_display_history(failed.state, updated).model_dump_json())
    assert saved_display_history(saved) == updated


def test_reasoning_parts_with_shared_provider_id_stay_separate_in_live_and_saved_display() -> None:
    from datetime import UTC, datetime

    from a13n_harness import HarnessEvent
    from pydantic_ai.messages import PartEndEvent, PartStartEvent, ThinkingPart

    collector = DisplayHistoryCollector(())
    parts = [ThinkingPart(text, id="rs_shared") for text in ("**First plan**", "**Second plan**")]
    for index, part in enumerate(parts):
        for offset, event in enumerate((PartStartEvent(index=index, part=part), PartEndEvent(index=index, part=part))):
            collector.observe(
                HarnessEvent(
                    thread_id="thread",
                    run_id="run",
                    sequence=index * 2 + offset,
                    occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
                    event=event,
                )
            )
    publication = collector.drain()
    assert publication is not None
    assert [item.content["text"] for item in publication.items if item.kind == "reasoning_message"] == [
        part.content for part in parts
    ]
    native = HarnessState.new(message_history=[ModelResponse(parts=parts)])
    saved = with_display_history(native, collector.capture(native.message_history))
    restored = saved_display_history(HarnessState.model_validate_json(saved.model_dump_json()))
    assert restored is not None
    assert visible(restored) == [part.content for part in parts]
    assert [part.kind for entry in display_entries(restored) for part in entry.parts] == ["thinking", "thinking"]


def test_legacy_compact_migration_preserves_full_original_message_part_addresses() -> None:
    from a13n_harness.state import AgentContextStateSnapshot, CapabilityState
    from a13n_harness_ui.display_history import _message_digest
    from a13n_harness_ui.display_projection import original_text
    from pydantic_ai.messages import ThinkingPart

    text = "original " * 40000
    native = HarnessState.new(
        message_history=[
            ModelRequest(parts=[UserPromptPart("Question")]),
            ModelResponse(parts=[ThinkingPart("Thinking"), TextPart(text), TextPart("Same"), TextPart("Same")]),
        ]
    )
    old = native.model_copy(
        update={
            "agent_context_state": AgentContextStateSnapshot(
                entries={
                    "a13n.harness-ui.display-history": CapabilityState(
                        version="1",
                        data={
                            "messages": native.model_dump(mode="json")["message_history"],
                            "model_positions": [0, 1],
                            "pending_response_position": None,
                            "model_history_digest": _message_digest(native.message_history_json),
                        },
                    ),
                }
            )
        }
    )
    display = saved_display_history(old)
    assert display is not None
    assert original_text(display, 1, 1) == text
    assert original_text(display, 1, 2) == original_text(display, 1, 3) == "Same"
    assert original_text(display, 1, 0) is None
    compact = with_display_history(native, display)
    envelope = compact.agent_context_state.get("a13n.harness-ui.display-history")
    assert envelope.version == "2" and "messages" not in envelope.data
    restored = saved_display_history(HarnessState.model_validate_json(compact.model_dump_json()))
    assert restored is not None and original_text(restored, 1, 1) == text
    preview = display_entries(restored)[1].parts[1]
    assert preview.text_truncated and len(preview.text) <= 65536


def test_failed_live_publication_retries_a_baseline_without_losing_durable_capture() -> None:
    from datetime import UTC, datetime

    from a13n_harness import HarnessEvent
    from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPartDelta

    collector = DisplayHistoryCollector(())

    def observe(sequence, event):
        collector.observe(
            HarnessEvent(
                thread_id="thread", run_id="run", sequence=sequence, occurred_at=datetime.now(UTC), event=event
            )
        )

    observe(1, PartStartEvent(index=0, part=TextPart("first")))
    collector.drain()
    collector.publication_failed()
    observe(2, PartDeltaEvent(index=0, delta=TextPartDelta(" second")))
    retry = collector.drain()
    assert retry is not None and retry.reset and not retry.observations
    items = {item.id: item for item in retry.items}
    assert any(item.content.get("text") == "first second" for item in items.values())
    assert visible(collector.capture()) == ["first second"]
    assert collector.fold.observer.event_count == 0


def test_capture_and_supplements_share_one_dirty_set_without_consuming_publication() -> None:
    from datetime import UTC, datetime

    from a13n_harness import HarnessEvent
    from ag_ui.core import CustomEvent
    from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPartDelta, ToolCallPart

    collector = DisplayHistoryCollector(())

    def observe(sequence, event):
        collector.observe(
            HarnessEvent(
                thread_id="thread", run_id="run", sequence=sequence, occurred_at=datetime.now(UTC), event=event
            )
        )

    observe(1, PartStartEvent(index=0, part=TextPart("first")))
    observe(2, PartDeltaEvent(index=0, delta=TextPartDelta(" second")))
    observe(3, PartStartEvent(index=1, part=ToolCallPart("inspect", {}, "call")))
    collector.supplement(
        [
            CustomEvent(
                name="a13n.harness-ui.tool_images",
                value={"event": {"tool_call_id": "call", "images": [{"path": "retained.png"}]}},
            )
        ]
    )
    frozen = collector.capture()
    frozen_json = frozen.model_dump_json()
    changes = collector.drain()
    assert changes is not None and not changes.reset
    assert changes.items == frozen.items
    assert changes.continuation == frozen.continuation
    assert len({item.id for item in changes.items}) == len(changes.items)
    assert collector.drain() is None
    assert next(item for item in frozen.items if item.kind == "tool_call").content["tool_images"] == [
        {"path": "retained.png"}
    ]

    observe(4, PartDeltaEvent(index=0, delta=TextPartDelta(" third")))
    appended = collector.drain()
    assert appended is not None and len(appended.items) == 1
    assert any(o.event.get("delta") == " third" for o in appended.observations)
    collector.publication_failed()
    observe(5, PartDeltaEvent(index=0, delta=TextPartDelta(" fourth")))
    retry = collector.drain()
    assert retry is not None and retry.reset and not retry.observations
    items = {item.id: item for item in retry.items}
    assert tuple(items.values()) == collector.capture().items
    assert any(item.content.get("text") == "first second third fourth" for item in items.values())
    assert collector.drain() is None
    assert frozen.model_dump_json() == frozen_json


def test_tool_result_does_not_renumber_later_comment_parts() -> None:
    from a13n_stream_protocol.display import DisplayFold

    fold = DisplayFold("run", full_content=True)
    fold.fold(
        [
            {"type": "TOOL_CALL_START", "toolCallId": "call", "toolCallName": "inspect"},
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": "answer", "delta": "Comment here"},
        ]
    )
    # Both items belong to the same native response, even when the result arrives later.
    for key, item in fold.items.items():
        fold.items[key] = item.model_copy(update={"content": {**item.content, "responseGroup": "response"}})

    def address():
        entries = display_entries(DisplayHistory(items=tuple(fold.items.values())))
        return next(
            (entry.position, index)
            for entry in entries
            for index, part in enumerate(entry.parts)
            if part.text == "Comment here"
        )

    before = address()
    fold.fold([{"type": "TOOL_CALL_RESULT", "toolCallId": "call", "content": "done"}])
    assert address() == before


@pytest.mark.parametrize("closing", ["complete", "suspended", "empty", "compaction", "after-final"])
def test_imported_turns_preserve_native_output_and_completion_semantics(closing: str) -> None:
    from a13n_harness_ui.display_history import import_display_history
    from a13n_harness_ui.display_projection import display_turns
    from a13n_harness_ui.thread_projection import _transcript_turns

    history = [ModelRequest(parts=[UserPromptPart("Question")]), ModelResponse(parts=[TextPart("Answer")])]
    completed = ()
    if closing == "suspended":
        history[-1].state = "suspended"
    elif closing == "empty":
        history[-1].parts = [TextPart("   ")]
    elif closing == "compaction":
        history[-1].metadata = {"keep": "compact"}
    elif closing == "after-final":
        history[-1].metadata = {"a13n.harness-ui.completed": True}
        completed = (1,)
        history.append(ModelRequest(parts=[UserPromptPart("Steer")], metadata={"a13n.steering-run": "run-one"}))
    assert display_turns(import_display_history(history)) == _transcript_turns(tuple(history), completed)


def test_saved_run_errors_are_inspectable_without_renumbering_comment_targets():
    from a13n_harness_ui.display_projection import original_text
    from a13n_stream_protocol.display import DisplayFold

    fold = DisplayFold("run-old", full_content=True)
    fold.fold(
        [
            {"type": "RUN_STARTED", "threadId": "thread", "runId": "run-old"},
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": "partial", "delta": "Partial answer"},
            {"type": "RUN_ERROR", "message": "Provider failed", "code": "provider_failed"},
            {"type": "RUN_STARTED", "threadId": "thread", "runId": "run-new"},
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": "new", "delta": "New answer"},
        ]
    )
    display = DisplayHistory(items=tuple(fold.items.values()))
    entries = display_entries(display)
    assert len(entries) == 2
    assert entries[0].failures[0].run_id == "run-old"
    assert entries[0].failures[0].message == "Provider failed"
    assert entries[1].failures == ()
    assert original_text(display, 0, 0) == "Partial answer"
    assert original_text(display, 1, 0) == "New answer"
