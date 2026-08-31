from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from a13n_harness.filters import (
    ColdStartFilterCapability,
    ColdStartFilterConfiguration,
    MessageIntegrityFilterCapability,
)
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.models.function import FunctionModel

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
