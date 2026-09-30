"""Native execution, live input and both saved-history paths agree on provenance."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from a13n_harness import AgentContext, HarnessBuilder, HarnessState, RunBindings
from a13n_harness.capabilities import CodeActCapability
from a13n_harness.filters import ContentFilterCapability, ContentFilterConfiguration
from a13n_harness.model_context import user_prompt_content
from a13n_harness.toolsets.codeact import CodeActPolicyToolset, CodeActToolPolicy
from a13n_harness_ui.conversation import ConversationExcerpt, checkpoint_excerpt
from a13n_harness_ui.display_history import DisplayHistoryCollector, saved_display_history, with_display_history
from a13n_harness_ui.thread_projection import _message_entry, _transcript_turns
from a13n_stream_protocol import HarnessAguiObserver
from pydantic_ai import BinaryContent, RunContext, TextContent, ToolReturn
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability, Hooks
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("runner", [False, True], ids=["direct", "codeact"])
@pytest.mark.parametrize("shape", ["scalar", "list", "tuple", "media", "filtered"])
async def test_supplemental_content_never_becomes_authored_input(runner: bool, shape: str) -> None:
    # Identical authored and injected text must remain distinguishable by origin.
    original_text = TextContent("same", metadata={"source_id": "attachment", "custom": "keep", "display": True})
    original_media = BinaryContent(b"1234", media_type="image/png", vendor_metadata={"source_id": "attachment"})
    supplemental = (
        "same"
        if shape == "scalar"
        else (original_text, "same")
        if shape == "tuple"
        else [original_media]
        if shape in {"media", "filtered"}
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
                    json_args=json.dumps({"code": "await attach(value=1)"}) if runner else '{"value":1}',
                    tool_call_id="attach-1",
                )
            }
            return
        # Supplement reaches the model alongside genuine steering, not a blanket
        # hidden tool-result request. The provider text/media itself is retained.
        content = [
            item
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
            for item in user_prompt_content(part)
        ]
        injected = [
            item
            for item in content
            if (isinstance(item, TextContent) and (item.metadata or {}).get("source_id") in {"attachment", "a13n.tool"})
            or (isinstance(item, BinaryContent) and (item.vendor_metadata or {}).get("source_id") == "attachment")
        ]
        assert injected
        assert all(
            (item.metadata if isinstance(item, TextContent) else item.vendor_metadata)["display"] is False
            for item in injected
        )
        if shape == "filtered":
            assert isinstance(injected[0], TextContent)
            assert "exceeds request limits" in injected[0].content
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
    observer = HarnessAguiObserver()
    events = []
    async with executable.stream("same", bindings=RunBindings.embedded()) as run:
        async for item in run:
            events.extend(event.model_dump(mode="json") for event in observer.observe(item))
    result = run.result
    assert result is not None
    assert result.output_or_raise() == "done"
    assert [
        event["delta"]
        for event in events
        if event["type"] == "TEXT_MESSAGE_CONTENT"
        and event.get("role") == "user"
        and event.get("metadata", {}).get("display", True)
    ] == ["same", "same"]

    # Canonical continuation and the separate retained display survive export/reload.
    state = HarnessState.model_validate_json(result.state.model_dump_json())
    collector = DisplayHistoryCollector([])
    display = collector.capture(state.message_history, completed=True)
    saved = HarnessState.model_validate_json(with_display_history(state, display).model_dump_json())
    restored = saved_display_history(saved)
    assert restored is not None
    for history in (state.message_history, restored.messages):
        assert [turn.preview for turn in _transcript_turns(history)] == ["same", "same"]
        assert checkpoint_excerpt(ConversationExcerpt(), history).latest_input == "same"
        parts = [part for index, message in enumerate(history) for part in _message_entry(index, message).parts]
        visible_users = [part for part in parts if part.kind in {"user", "media"} and part.metadata.display]
        assert [part.text for part in visible_users] == ["same", "same"]
        assert any(part.kind == "tool_result" for part in parts)
    assert original_text.metadata == {"source_id": "attachment", "custom": "keep", "display": True}
    assert original_media.vendor_metadata == {"source_id": "attachment"}


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
    supplement = next(part for part in parts if part.metadata.source_id == "a13n.tool")
    assert supplement.text == "real input" and supplement.metadata.display is False
