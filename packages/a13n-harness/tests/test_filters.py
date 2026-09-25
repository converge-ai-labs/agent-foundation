from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from a13n_harness import AgentSpec as HarnessAgentSpec
from a13n_harness import HarnessBuilder, HarnessState, RunBindings
from a13n_harness.capability_types import CapabilityTypeCatalog
from a13n_harness.filters import (
    ColdStartFilterCapability,
    ColdStartFilterConfiguration,
    MessageIntegrityFilterCapability,
)
from pydantic_ai import AgentSpec
from pydantic_ai.capabilities import CombinedCapability, PrefixTools, WrapperCapability
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

pytestmark = pytest.mark.anyio


def _request_context(messages):
    async def model(messages, info):
        del messages, info
        return "unused"

    return ModelRequestContext(
        model=FunctionModel(model),
        messages=messages,
        model_settings=None,
        model_request_parameters=ModelRequestParameters(),
    )


async def test_message_integrity_filter_keeps_only_one_result_for_the_current_call() -> None:
    current = ToolReturnPart(tool_name="lookup", tool_call_id="call-2", content="current")
    duplicate = ToolReturnPart(tool_name="lookup", tool_call_id="call-2", content="duplicate")
    orphan = ToolReturnPart(tool_name="lookup", tool_call_id="call-1", content="orphan")
    messages = [
        ModelResponse(parts=[ToolCallPart("lookup", {}, tool_call_id="call-1")]),
        ModelResponse(parts=[ToolCallPart("lookup", {}, tool_call_id="call-2")]),
        ModelRequest(parts=[orphan, current, duplicate, UserPromptPart("continue")]),
    ]

    filtered = await MessageIntegrityFilterCapability().before_model_request(
        None,  # type: ignore[arg-type]
        _request_context(messages),
    )

    request = filtered.messages[-1]
    assert isinstance(request, ModelRequest)
    tool_results = [part for part in request.parts if isinstance(part, ToolReturnPart)]
    assert tool_results == [current]
    assert filtered.messages is not messages
    assert messages[-1].parts[:3] == [orphan, current, duplicate]


async def test_message_integrity_filter_persists_the_processed_history() -> None:
    current = ToolReturnPart(tool_name="lookup", tool_call_id="call-2", content="current")
    duplicate = ToolReturnPart(tool_name="lookup", tool_call_id="call-2", content="duplicate")
    orphan = ToolReturnPart(tool_name="lookup", tool_call_id="call-1", content="orphan")
    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart("start")]),
            ModelResponse(parts=[ToolCallPart("lookup", {}, tool_call_id="call-2")]),
            ModelRequest(parts=[orphan, current, duplicate, UserPromptPart("continue")]),
        )
    )

    seen: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        seen.append(messages)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )
    result = await executable.run("next", bindings=RunBindings.embedded(), previous_state=previous)

    assert result.state is not None

    def tool_results(messages: list[ModelMessage] | tuple[ModelMessage, ...]) -> list[ToolReturnPart]:
        return [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]

    assert tool_results(seen[0]) == [current]
    assert tool_results(result.all_messages()) == [current]
    assert tool_results(result.state.message_history) == [current]


async def test_message_integrity_filter_preserves_model_level_retry_and_final_request() -> None:
    retry = RetryPromptPart(content="try again")
    orphan = ToolReturnPart(tool_name="lookup", tool_call_id="call-1", content="orphan")
    messages = [
        ModelResponse(parts=[ToolCallPart("lookup", {}, tool_call_id="call-1")]),
        ModelRequest(parts=[retry, orphan]),
    ]

    filtered = await MessageIntegrityFilterCapability().before_model_request(
        None,  # type: ignore[arg-type]
        _request_context(messages),
    )

    assert len(filtered.messages) == 2
    request = filtered.messages[-1]
    assert isinstance(request, ModelRequest)
    assert request.parts == (retry,)


@pytest.mark.parametrize(
    ("spec", "idle_seconds", "trimmed"),
    [
        (HarnessAgentSpec(), 3590, False),
        (HarnessAgentSpec(), 3610, True),
        (AgentSpec(), 3610, True),
        (HarnessAgentSpec(cold_start_filter=None), 7200, False),
        (HarnessAgentSpec(cold_start_filter=ColdStartFilterConfiguration(idle_seconds=7200)), 3610, False),
    ],
)
async def test_agent_spec_cold_start_policy_commits_only_consumed_results(
    spec: AgentSpec, idle_seconds: int, trimmed: bool
) -> None:
    old_text = "a" * 4000
    current_text = "b" * 4000
    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart("Original input")]),
            ModelResponse(parts=[ToolCallPart("read", {}, tool_call_id="old")]),
            ModelRequest(parts=[ToolReturnPart("read", old_text, tool_call_id="old")]),
            ModelResponse(
                parts=[TextPart("Consumed"), ToolCallPart("read", {}, tool_call_id="pending")],
                timestamp=datetime.now(UTC) - timedelta(seconds=idle_seconds),
            ),
            ModelRequest(parts=[ToolReturnPart("read", current_text, tool_call_id="pending")]),
        )
    )
    seen: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        seen.append(messages)
        yield "done"

    executable = HarnessBuilder().build(spec, output_type=str, model=FunctionModel(stream_function=stream))
    result = await executable.run("Continue", previous_state=previous, bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    assert result.state is not None
    for messages in (seen[0], result.state.message_history):
        returns = {
            part.tool_call_id: part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        }
        assert ("chars removed after cold start" in returns["old"]) is trimmed
        assert returns["pending"] == current_text
    original = previous.message_history[2]
    assert isinstance(original, ModelRequest)
    assert original.parts[0].content == old_text


@pytest.mark.parametrize("composition", ["direct", "combined", "wrapped"])
def test_explicit_cold_start_capability_retains_its_policy(composition: str) -> None:
    explicit = ColdStartFilterCapability(ColdStartFilterConfiguration(idle_seconds=7200))
    capability = (
        CombinedCapability([explicit])
        if composition == "combined"
        else PrefixTools(wrapped=explicit, prefix="custom", id="test.wrapper")
        if composition == "wrapped"
        else explicit
    )
    # The automatic filter stays enabled, so a single filter proves the explicit one replaced it.
    executable = HarnessBuilder().build(
        HarnessAgentSpec(), output_type=str, model=TestModel(), capabilities=(capability,)
    )
    leaves = []
    executable._agent.root_capability.apply(leaves.append)
    filters = []
    for item in leaves:
        while isinstance(item, WrapperCapability):
            item = item.wrapped
        if isinstance(item, ColdStartFilterCapability):
            filters.append(item)
    assert len(filters) == 1
    assert filters[0].configuration.idle_seconds == 7200


@pytest.mark.parametrize("wrapped", [False, True])
def test_declarative_cold_start_capability_suppresses_default(wrapped: bool) -> None:
    declaration = {
        "name": "ColdStartFilterCapability",
        "arguments": {"configuration": {"idle_seconds": 7200}},
    }
    if wrapped:
        declaration = {"name": "PrefixTools", "arguments": {"prefix": "x", "capability": declaration}}
    spec = HarnessAgentSpec.from_dict({"capabilities": [declaration]})
    builder = HarnessBuilder(capability_type_catalog=CapabilityTypeCatalog.from_types((ColdStartFilterCapability,)))
    executable = builder.build(spec, output_type=str, model=TestModel())
    leaves = []
    executable._agent.root_capability.apply(leaves.append)
    filters = []
    for item in leaves:
        while isinstance(item, WrapperCapability):
            item = item.wrapped
        if isinstance(item, ColdStartFilterCapability):
            filters.append(item)
    assert len(filters) == 1
    assert filters[0].configuration.idle_seconds == 7200


async def test_cold_start_filter_trims_only_consumed_tool_return_string_leaves() -> None:
    old_text = "a" * 400
    current_text = "b" * 400
    old = ToolReturnPart(
        tool_name="read",
        tool_call_id="call-1",
        content={"content": old_text, "hint": "preserved"},
    )
    current = ToolReturnPart(tool_name="read", tool_call_id="call-2", content=current_text)
    messages = [
        ModelResponse(parts=[ToolCallPart("read", {}, tool_call_id="call-1")]),
        ModelRequest(parts=[old]),
        ModelResponse(
            parts=[TextPart("processed"), ToolCallPart("read", {}, tool_call_id="call-2")],
            timestamp=datetime.now(UTC) - timedelta(hours=2),
        ),
        ModelRequest(parts=[current]),
    ]
    capability = ColdStartFilterCapability(
        ColdStartFilterConfiguration(
            idle_seconds=60,
            max_string_chars=128,
            keep_head_chars=40,
            keep_tail_chars=40,
        )
    )

    filtered = await capability.before_model_request(
        None,  # type: ignore[arg-type]
        _request_context(messages),
    )

    old_request = filtered.messages[1]
    assert isinstance(old_request, ModelRequest)
    old_result = old_request.parts[0]
    assert isinstance(old_result, ToolReturnPart)
    assert old_result.content["hint"] == "preserved"
    assert "chars removed after cold start" in old_result.content["content"]
    current_request = filtered.messages[-1]
    assert isinstance(current_request, ModelRequest)
    assert current_request.parts[0].content == current_text
    assert old.content["content"] == old_text


@pytest.mark.parametrize(
    ("metadata", "preserved"),
    [
        ({"a13n.cold-start": "preserve", "other": {"value": 1}}, True),
        (None, False),
        ({"a13n.cold-start": True}, False),
        ("preserve", False),
    ],
)
async def test_cold_start_only_honors_result_metadata_not_content(metadata, preserved: bool) -> None:
    part = ToolReturnPart(
        "view",
        {"content": "x" * 4000, "a13n.cold-start": "preserve"},
        metadata=metadata,
    )
    request = _request_context(
        [
            ModelRequest(parts=[part]),
            ModelResponse(parts=[TextPart("consumed")], timestamp=datetime.now(UTC) - timedelta(hours=2)),
        ]
    )
    filtered = await ColdStartFilterCapability().before_model_request(None, request)
    actual = filtered.messages[0].parts[0]
    assert actual.metadata == metadata
    assert ("chars removed after cold start" in actual.content["content"]) is not preserved
    assert (filtered is request) is preserved
    assert part.content["content"] == "x" * 4000


@pytest.mark.parametrize("kind", ["integrity", "cold_start", "content"])
@pytest.mark.parametrize("changed", [False, True])
async def test_filters_copy_only_changed_history_and_detach_all_nested_values(kind, changed, monkeypatch):
    from copy import deepcopy
    from unittest.mock import Mock

    from a13n_harness.filters import cold_start, content, integrity
    from pydantic_ai import ImageUrl

    modules = {"integrity": integrity, "cold_start": cold_start, "content": content}
    capabilities = {
        "integrity": MessageIntegrityFilterCapability(),
        "cold_start": ColdStartFilterCapability(),
        "content": content.ContentFilterCapability(),
    }
    part = ToolReturnPart(
        tool_name="lookup",
        tool_call_id="orphan" if changed and kind == "integrity" else "call-1",
        content={"value": "x" * 3000 if changed and kind == "cold_start" else "short"},
    )
    image = ImageUrl("https://example.com/image.png" + ("?api_key=secret" if changed and kind == "content" else ""))
    messages = [
        ModelResponse(parts=[ToolCallPart("lookup", {"nested": [1]}, tool_call_id="call-1")]),
        ModelRequest(parts=[part, UserPromptPart([image])]),
        ModelResponse(parts=[TextPart("consumed")], timestamp=datetime.now(UTC) - timedelta(hours=2)),
    ]
    original = deepcopy(messages)
    request = _request_context(messages)
    copy_spy = Mock(wraps=deepcopy)
    monkeypatch.setattr(modules[kind], "deepcopy", copy_spy)

    filtered = await capabilities[kind].before_model_request(None, request)

    assert messages == original
    assert copy_spy.call_count == int(changed)
    if not changed:
        assert filtered is request
        return
    assert filtered is not request
    # Even unchanged nested values in a changed request remain detached.
    filtered.messages[0].parts[0].args["nested"].append(2)
    assert messages[0].parts[0].args == {"nested": [1]}
