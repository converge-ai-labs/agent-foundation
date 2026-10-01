"""AG-UI 1.0 lifecycle, attribution, and visibility regressions."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from a13n_harness import (
    AgentDefinition,
    AgentSpec,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessRunResult,
    HarnessRunResultEvent,
    RunBindings,
    SubagentDefinition,
)
from a13n_harness.capabilities import SubagentCapability
from a13n_harness.content import ContentItem, ContentMetadata
from a13n_harness.events import InlineDelegationPayload
from a13n_harness.tools import InvocationPolicyCapability, InvocationPolicyDecision
from a13n_harness.usage import RunUsageSummary
from a13n_stream_protocol import (
    AguiObservationError,
    HarnessAguiObserver,
    HarnessAguiStreamObserver,
    tool_result_content,
)
from ag_ui.core import CustomEvent, ImagePart, RunFinishedEvent, SubagentStartedEvent, TextMessageContentEvent
from pydantic_ai.messages import (
    BinaryContent,
    FunctionToolResultEvent,
    ImageUrl,
    ModelMessage,
    ModelRequest,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UploadedFile,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel


def source(event, run="root", thread="thread"):
    return HarnessEvent(thread_id=thread, run_id=run, sequence=0, occurred_at=datetime.now(UTC), event=event)


def start(run="root"):
    return source(HarnessExtensionEvent(kind="lifecycle", payload={"type": "run_started"}), run)


def delegation(action, child="child", parent="root"):
    return source(
        HarnessExtensionEvent(
            kind="delegation",
            payload=InlineDelegationPayload(
                invocation_id=f"delegation-{child}",
                action=action,
                child_instance_id=child,
                child_run_id=child,
                parent_run_id=parent,
                parent_agent_instance_id="agent",
                parent_tool_call_id="call",
                subagent="reviewer",
                status="completed" if action == "completed" else "running",
            ).model_dump(),
        ),
        child if action == "started" else parent,
    )


def test_interleaved_inline_parts_are_scoped_and_never_start_nested_runs():
    observer = HarnessAguiStreamObserver()
    observer.observe(start())
    observer.observe(source(PartStartEvent(index=0, part=TextPart("parent", id="same"))))
    assert observer.observe(start("child")) == ()
    started = observer.observe(delegation("started"))
    assert isinstance(started[0], SubagentStartedEvent)
    assert started[0].parent_tool_call_id == "call"
    child = observer.observe(source(PartStartEvent(index=0, part=TextPart("child", id="same")), "child"))
    assert all(event.subagent_run_id == "child" for event in child)
    parent = observer.observe(source(PartDeltaEvent(index=0, delta=TextPartDelta(" still parent"))))
    assert isinstance(parent[0], TextMessageContentEvent)
    assert parent[0].message_id == "same" and parent[0].subagent_run_id is None
    ended = observer.observe(delegation("completed"))
    assert [event.type for event in ended] == ["TEXT_MESSAGE_END", "SUBAGENT_FINISHED", "CUSTOM"]
    assert ended[0].subagent_run_id == "child"
    observer.observe(source(PartEndEvent(index=0, part=TextPart("parent still parent", id="same"))))
    assert sum(event.type == "RUN_STARTED" for event in observer.snapshot()) == 1
    assert observer.run_id == "root"


def test_nested_inline_lifecycle_retains_parent_and_atomic_correlation():
    observer = HarnessAguiStreamObserver()
    observer.observe(start())
    observer.observe(start("child"))
    observer.observe(delegation("started"))
    observer.observe(start("grandchild"))
    event = observer.observe(delegation("started", "grandchild", "child"))[0]
    assert event.parent_subagent_run_id == "child"
    before = observer.snapshot()
    with pytest.raises(AguiObservationError, match="Thread correlation"):
        observer.observe(source(PartDeltaEvent(index=0, delta=TextPartDelta("bad")), "child", "other"))
    assert observer.snapshot() == before


def test_cancelled_terminal_settles_only_open_presentations_and_keeps_cache_write_usage():
    observer = HarnessAguiObserver()
    observer.observe(source(PartStartEvent(index=0, part=TextPart("partial"))))
    observer.observe(source(PartStartEvent(index=1, part=ThinkingPart("reason"))))
    observer.observe(source(PartStartEvent(index=2, part=ToolCallPart("unfinished", tool_call_id="call"))))
    result = HarnessRunResult(
        thread_id="thread",
        run_id="root",
        status="cancelled",
        output=None,
        state=None,
        usage=RunUsageSummary(cache_write_tokens=11),
    )
    events = observer.observe(
        HarnessRunResultEvent(
            thread_id="thread", run_id="root", sequence=3, occurred_at=datetime.now(UTC), result=result
        )
    )
    assert [event.type for event in events] == ["TEXT_MESSAGE_END", "REASONING_MESSAGE_END", "RUN_FINISHED"]
    terminal = events[-1]
    assert isinstance(terminal, RunFinishedEvent) and terminal.outcome.type == "cancelled"
    assert terminal.usage[0].cache_write_input_tokens == 11
    assert terminal.model_dump(mode="json", by_alias=True)["usage"][0]["cacheWriteInputTokens"] == 11


@pytest.mark.parametrize("update", [{"subagent_run_id": "forged"}, {"delta": 42}, {"unexpected": True}])
def test_processor_cannot_inject_absent_structural_fields_or_invalid_content(update):
    observer = HarnessAguiObserver(processor=lambda _, event: event.model_copy(update=update))
    with pytest.raises(AguiObservationError):
        observer.observe(source(PartDeltaEvent(index=0, delta=TextPartDelta("hello"))))
    assert observer.snapshot() == ()


def test_required_null_custom_payload_is_not_dropped():
    event = CustomEvent(name="a13n.test", value=None)
    assert event.model_dump(mode="json", by_alias=True) == {"type": "CUSTOM", "name": "a13n.test", "value": None}


def test_tool_parts_preserve_order_duplicates_and_hide_native_bytes():
    image = ImageUrl("https://example.com/a.png")
    parts = tool_result_content(
        [
            "before",
            image,
            image,
            ContentItem(image, ContentMetadata(display=False)),
            BinaryContent(b"secret-media-bytes", media_type="image/png"),
            "after",
        ]
    )
    assert isinstance(parts, list)
    assert [part.type for part in parts] == ["text", "image", "image", "text", "text"]
    assert isinstance(parts[1], ImagePart) and parts[1].source.value == image.url
    assert "secret-media-bytes" not in "".join(part.model_dump_json() for part in parts)
    assert "payload_omitted" in parts[3].text
    assert tool_result_content({"bytes": b"secret-media-bytes"}) == '{"bytes":{"payload_omitted":true,"size_bytes":18}}'


@pytest.mark.parametrize("outcome", ["success", "failed", "denied"])
def test_url_and_provider_supplements_never_become_public_parts(outcome):
    private = [
        ImageUrl("https://example.com/private.png"),
        UploadedFile(file_id="private-id", provider_name="openai", media_type="application/pdf"),
    ]
    event = FunctionToolResultEvent(
        ToolReturnPart(
            tool_name="view",
            tool_call_id="call",
            outcome=outcome,
            content=["execution", *private],
            metadata={"a13n.tool-content": {"result_index": 0, "items": [{"display": False}, {"display": False}]}},
        ),
        content=private,
    )
    observed = HarnessAguiObserver().observe(source(event))
    encoded = "".join(item.model_dump_json() for item in observed)
    assert "execution" in encoded
    assert "private.png" not in encoded and "private-id" not in encoded


@pytest.mark.anyio
async def test_stream_resume_is_atomic_for_interleaved_children_and_continues_parts():
    prefix = [
        start(),
        start("child"),
        delegation("started"),
        source(PartStartEvent(index=0, part=TextPart("child", id="same")), "child"),
        source(PartStartEvent(index=0, part=TextPart("root", id="same"))),
    ]

    async def history(fail=False):
        for item in prefix:
            yield item
        if fail:
            raise RuntimeError("history unavailable")

    observer = HarnessAguiStreamObserver()
    with pytest.raises(RuntimeError, match="history unavailable"):
        await observer.resume(history(True))
    assert observer.run_id is None and observer.snapshot() == ()
    await observer.resume(history())
    direct = HarnessAguiStreamObserver()
    for item in prefix:
        direct.observe(item)
    assert observer.snapshot() == direct.snapshot()
    delta = source(PartDeltaEvent(index=0, delta=TextPartDelta(" more")), "child")
    assert observer.observe(delta) == direct.observe(delta)
    completed = delegation("completed")
    assert observer.observe(completed) == direct.observe(completed)


@pytest.mark.anyio
async def test_real_inline_harness_stream_projects_child_before_authoritative_terminal():
    async def child_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "child answer"

    async def parent_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        ):
            yield "parent answer"
        else:
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args='{"subagent":"reviewer","prompt":"inspect"}',
                    tool_call_id="delegate-call",
                )
            }

    async def allow(*args, **kwargs):
        return InvocationPolicyDecision.allow()

    child = AgentDefinition(agent=AgentSpec(), output_type=str, model=FunctionModel(stream_function=child_stream))
    parent = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=parent_stream),
        capabilities=(SubagentCapability(),),
        subagents=(SubagentDefinition(name="reviewer", description="Inspect one task", agent=child),),
    )
    executable = HarnessBuilder().build(parent)
    observer = HarnessAguiStreamObserver()
    async with executable.stream(
        "review", bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=allow),))
    ) as stream:
        async for item in stream:
            observer.observe(item)
    events = observer.snapshot()
    assert sum(event.type == "RUN_STARTED" for event in events) == 1
    assert sum(event.type == "RUN_FINISHED" for event in events) == 1
    child_start = next(event for event in events if event.type == "SUBAGENT_STARTED")
    child_id = child_start.subagent_run_id
    child_text = next(
        event for event in events if isinstance(event, TextMessageContentEvent) and event.delta == "child answer"
    )
    assert child_text.subagent_run_id == child_id
    child_end = next(event for event in events if event.type == "SUBAGENT_FINISHED")
    assert events.index(child_start) < events.index(child_text) < events.index(child_end)
    assert events[-1].type == "RUN_FINISHED"
    terminal_fact = events[events.index(child_end) + 1]
    assert isinstance(terminal_fact, CustomEvent)
    assert terminal_fact.value["event"]["payload"]["action"] == "completed"
