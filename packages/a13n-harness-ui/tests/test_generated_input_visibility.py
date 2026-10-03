"""Native execution, live input and both saved-history paths agree on provenance."""

from __future__ import annotations

import base64
import io
import json
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from a13n_harness import AgentContext, HarnessBuilder, HarnessState, RunBindings
from a13n_harness.capabilities import CodeActCapability
from a13n_harness.content import request_input_content
from a13n_harness.filters import ContentFilterCapability, ContentFilterConfiguration
from a13n_harness.tools._output import TOOL_CONTENT_METADATA_KEY, tool_execution_value
from a13n_harness.toolsets.codeact import CodeActPolicyToolset, CodeActToolPolicy
from a13n_harness_ui.conversation import ConversationExcerpt, checkpoint_excerpt
from a13n_harness_ui.display_history import DisplayHistory
from a13n_harness_ui.display_projection import display_turns
from a13n_harness_ui.thread_projection import _message_entry, _transcript_turns
from a13n_stream_protocol import AUTHORED_INPUT_EVENT_NAMES, HarnessAguiObserver
from PIL import Image
from pydantic_ai import BinaryContent, RunContext, TextContent, ToolReturn
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability, Hooks
from pydantic_ai.messages import CachePoint, ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

pytestmark = pytest.mark.anyio


def _png() -> bytes:
    buffer = io.BytesIO()
    with Image.new("RGB", (1, 1), "red") as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.parametrize("runner", [False, True], ids=["direct", "codeact"])
@pytest.mark.parametrize("shape", ["scalar", "list", "tuple", "media", "filtered", "cache"])
async def test_supplemental_content_never_becomes_authored_input(runner: bool, shape: str) -> None:
    # Identical authored and injected text must remain distinguishable by origin.
    original_text = TextContent("same", metadata={"source_id": "attachment", "custom": "keep", "display": True})
    original_media = BinaryContent(_png(), media_type="image/png", vendor_metadata={"detail": "high"})
    supplemental = (
        "same"
        if shape == "scalar"
        else (original_text, "same")
        if shape == "tuple"
        else [original_media]
        if shape in {"media", "filtered"}
        else [original_text, CachePoint(ttl="1h")]
        if shape == "cache"
        else [original_text, "same"]
    )

    def attach(ctx: RunContext[AgentContext], value: int) -> ToolReturn:
        assert value == 1
        ctx.enqueue(TextContent("same", metadata={"source_id": "human-steering"}), priority="asap")
        return ToolReturn("tool readable value", content=supplemental)

    requests = 0

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests
        requests += 1
        if requests == 1:
            yield {
                0: DeltaToolCall(
                    name="run_code" if runner else "attach",
                    json_args=json.dumps(
                        {"code": "result = await attach(value=1)\nassert result == 'tool readable value'\nresult"}
                    )
                    if runner
                    else '{"value":1}',
                    tool_call_id="attach-1",
                )
            }
            return
        # Supplemental values belong to the native tool return, never user input.
        part = next(
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        )
        assert TOOL_CONTENT_METADATA_KEY in (part.metadata or {})
        assert isinstance(part.content, list)
        injected = part.content[1:]
        assert injected
        if shape == "filtered":
            assert isinstance(injected[0], str)
            assert "exceeds request limits" in injected[0]
        elif shape == "media":
            assert isinstance(injected[0], BinaryContent)
            assert injected[0].vendor_metadata == {"detail": "high"}
        prompts = [
            item.value
            for message in messages
            if isinstance(message, ModelRequest)
            for item in request_input_content(message)
        ]
        assert not any(isinstance(item, BinaryContent) for item in prompts)
        if shape == "cache":
            assert any(isinstance(item, CachePoint) and item.ttl == "1h" for item in prompts)
            assert not any(isinstance(item, CachePoint) for item in injected)
        yield "done"

    tools = Capability(tools=[attach], id="attachments")
    if runner:
        tools = Capability(
            toolsets=[
                CodeActPolicyToolset(
                    wrapped=FunctionToolset([attach], id="attachments"),
                    policy=CodeActToolPolicy(tools={"attach": True}),
                )
            ],
            id="attachments",
        )
    capabilities = [tools]
    if shape == "scalar" and not runner:

        async def replace_supplement(ctx, *, call, tool_def, args, result):
            return replace(result, content="same")

        capabilities.append(Hooks(after_tool_execute=replace_supplement, id="replace-supplement"))
    if runner:
        capabilities.append(CodeActCapability())
    if shape == "filtered":
        capabilities.append(ContentFilterCapability(ContentFilterConfiguration(max_binary_bytes=3)))
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=capabilities,
    )
    fold = DisplayHistory().start("run", resume=False)
    events = []
    async with executable.stream("same", bindings=RunBindings.embedded()) as run:
        async for item in run:
            batch = fold.events(item)
            fold.fold(batch, source=item)
            events.extend(batch)
    result = run.result
    assert result is not None
    assert result.output_or_raise() == "done"
    assert [
        event["value"]["event"]["content"]
        for event in events
        if event["type"] == "CUSTOM"
        and event.get("name") in AUTHORED_INPUT_EVENT_NAMES
        and event.get("metadata", {}).get("display", True)
    ] == ["same", "same"]

    # Canonical continuation and the separate retained display survive export/reload.
    state = HarnessState.model_validate_json(result.state.model_dump_json())
    display = DisplayHistory().capture(fold, completed=True)
    restored = DisplayHistory.model_validate_json(display.model_dump_json())
    # The second authored item is steering, not a new ordinary turn.
    assert [turn.preview for turn in display_turns(restored)] == ["same"]
    assert display_turns(restored)[0].steering_count == 1
    assert [
        item.content["text"]
        for item in restored.items
        if item.kind == "text_message"
        and item.content.get("role") == "user"
        and item.content.get("metadata", {}).get("display", True)
    ] == ["same", "same"]
    assert any(item.kind == "tool_call" and item.state == "completed" for item in restored.items)
    for history in (state.message_history,):
        assert [turn.preview for turn in _transcript_turns(history)] == ["same", "same"]
        assert checkpoint_excerpt(ConversationExcerpt(), history).latest_input == "same"
        parts = [part for index, message in enumerate(history) for part in _message_entry(index, message).parts]
        visible_users = [part for part in parts if part.kind in {"user", "media"} and part.metadata.display]
        assert [part.text for part in visible_users] == ["same", "same"]
        assert any(part.kind == "tool_result" for part in parts)
    assert original_text.metadata == {"source_id": "attachment", "custom": "keep", "display": True}
    assert original_media.vendor_metadata == {"detail": "high"}


async def test_resumed_external_supplement_is_hidden_without_hiding_the_tool_result() -> None:
    from a13n_harness import DeferredToolResume
    from pydantic_ai import DeferredToolResults
    from pydantic_ai.exceptions import CallDeferred

    def attach() -> str:
        raise CallDeferred()

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        if not any(part.part_kind == "tool-return" for message in messages for part in message.parts):
            yield {0: DeltaToolCall(name="attach", json_args="{}", tool_call_id="external-1")}
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=[Capability(tools=[attach], id="external")],
    )
    suspended = await executable.run("real input")
    assert suspended.deferred is not None
    supplied = ToolReturn("external result", content="real input")
    resume = DeferredToolResume(suspended.deferred, DeferredToolResults(calls={"external-1": supplied}))
    result = await executable.run(previous_state=suspended.state, deferred_resume=resume)
    assert result.output_or_raise() == "done"
    assert supplied.content == "real input"
    state = HarnessState.model_validate_json(result.state.model_dump_json())
    assert [turn.preview for turn in _transcript_turns(state.message_history)] == ["real input"]
    assert checkpoint_excerpt(ConversationExcerpt(), state.message_history).latest_input == "real input"
    parts = [
        part for index, message in enumerate(state.message_history) for part in _message_entry(index, message).parts
    ]
    assert [part.text for part in parts if part.kind == "user" and part.metadata.display] == ["real input"]
    assert any(part.kind == "tool_result" and part.value == "external result" for part in parts)
    tool = next(
        part
        for message in state.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    )
    assert tool_execution_value(tool.content, tool.metadata) == "external result"
    assert tool.content == ["external result", "real input"]


@pytest.mark.parametrize("runner", [False, True], ids=["direct", "codeact"])
async def test_inline_deferred_media_preserves_structured_execution_and_safe_observer(runner: bool) -> None:
    from pydantic_ai import DeferredToolResults
    from pydantic_ai.capabilities import HandleDeferredToolCalls
    from pydantic_ai.exceptions import CallDeferred

    def attach() -> dict:
        raise CallDeferred()

    image_bytes = _png()
    supplied = ToolReturn(
        {"values": [2, 3]}, content=[BinaryContent(image_bytes, media_type="image/png"), "supplement"]
    )

    async def handle(ctx, requests):
        return DeferredToolResults(calls={call.tool_call_id: supplied for call in requests.calls})

    requests = 0

    async def model(messages, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            yield {
                0: DeltaToolCall(
                    name="run_code" if runner else "attach",
                    json_args=json.dumps(
                        {
                            "code": "result = await attach()\nassert isinstance(result, dict)\nassert isinstance(result['values'], list)\nsum(result['values'])"
                        }
                    )
                    if runner
                    else "{}",
                    tool_call_id="inline-1",
                )
            }
        else:
            part = next(
                part
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            )
            value = tool_execution_value(part.content, part.metadata)
            if runner:
                assert value == 5
            else:
                assert value == {"values": [2, 3]}
            assert any(isinstance(item, BinaryContent) for item in part.content)
            yield "done"

    tools = Capability(tools=[attach], id="inline-tools")
    capabilities = [tools, HandleDeferredToolCalls(handler=handle, id="inline-handler")]
    if runner:
        capabilities[0] = Capability(
            toolsets=[
                CodeActPolicyToolset(
                    wrapped=FunctionToolset([attach], id="inline-tools"),
                    policy=CodeActToolPolicy(tools={"attach": True}),
                )
            ],
            id="inline-tools",
        )
        capabilities.append(CodeActCapability())
    executable = HarnessBuilder().build(
        AgentSpec(), model=FunctionModel(stream_function=model), output_type=str, capabilities=capabilities
    )
    observer = HarnessAguiObserver()
    events = []
    async with executable.stream("real input") as run:
        async for item in run:
            events.extend(event.model_dump(mode="json") for event in observer.observe(item))
    assert run.result is not None and run.result.output_or_raise() == "done"
    encoded = json.dumps(events)
    assert base64.b64encode(image_bytes).decode() not in encoded
    assert "supplement" not in encoded
    assert supplied.return_value == {"values": [2, 3]}
    assert supplied.content is not None
    state = HarnessState.model_validate_json(run.result.state.model_dump_json())
    assert [turn.preview for turn in _transcript_turns(state.message_history)] == ["real input"]
    assert checkpoint_excerpt(ConversationExcerpt(), state.message_history).latest_input == "real input"
