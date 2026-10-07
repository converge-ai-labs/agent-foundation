from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from a13n_harness.capabilities import ToolCallSource, ToolReviewRequest
from a13n_harness.tools._source import tool_call_source, tool_call_source_scope
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

pytestmark = pytest.mark.anyio


def _context(deps, call_id="call-1", messages=None):
    return RunContext(
        deps=deps,
        model=TestModel(),
        usage=RunUsage(),
        tool_call_id=call_id,
        tool_name="execute",
        messages=messages or [],
    )


def _response(call_id="call-1", response_id="resp_parent"):
    return ModelResponse(
        parts=[ToolCallPart("execute", {}, tool_call_id=call_id)],
        model_name="parent-model",
        provider_name="openai",
        provider_response_id=response_id,
    )


def test_source_matches_issuing_call_not_latest_response_and_survives_serialization():
    history = [_response(), ModelResponse(parts=[TextPart("compaction")], provider_response_id="resp_other")]
    restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(history))
    ctx = _context(object(), messages=restored)
    expected = ToolCallSource(model_name="parent-model", provider_name="openai", provider_response_id="resp_parent")
    assert tool_call_source(ctx) == expected
    assert tool_call_source(replace(ctx, tool_call_id="unknown")) is None
    assert tool_call_source(replace(ctx, tool_name="another_tool")) is None
    request = ToolReviewRequest(
        tool_id="tool/native/execute",
        tool_call_id="call-1",
        tool_name="execute",
        parameters_schema={},
        arguments={},
        source=expected,
    )
    assert "resp_parent" not in request.to_prompt()
    assert "parent-model" not in request.to_prompt()
    with pytest.raises(ValueError):
        expected.provider_response_id = "changed"


async def test_nested_sources_are_task_local_and_do_not_cross_agent_contexts():
    deps = object()
    entered = 0
    ready = asyncio.Event()

    async def invoke(index):
        nonlocal entered
        ctx = _context(deps, f"call-{index}", [_response(f"call-{index}", f"resp_{index}")])
        with tool_call_source_scope(ctx):
            entered += 1
            if entered == 2:
                ready.set()
            await ready.wait()
            nested = replace(ctx, tool_call_id=f"proxy-{index}", tool_name="target")
            assert tool_call_source(nested).provider_response_id == f"resp_{index}"
            assert tool_call_source(replace(nested, deps=object())) is None
            with tool_call_source_scope(nested):
                assert tool_call_source(nested).provider_response_id == f"resp_{index}"
        assert tool_call_source(nested) is None

    await asyncio.gather(invoke(1), invoke(2))


@pytest.mark.parametrize("response_id", [None, "x" * 4097])
def test_unavailable_source_is_not_invented(response_id):
    ctx = _context(object(), messages=[_response(response_id=response_id)])
    source = tool_call_source(ctx)
    assert source is None or source.provider_response_id is None
