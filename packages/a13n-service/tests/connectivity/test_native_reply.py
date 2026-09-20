"""Undelivered text is corrected by the model, never automatically published."""

import pytest
from a13n_harness import AgentSpec, HarnessBuilder
from a13n_service.connectivity.native_reply import NativeReplyCapability
from a13n_service.connectivity.toolsets import local_capability
from mcp.types import Tool
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("outcome", ["succeeded", "outcome_unknown"])
async def test_missing_reply_gets_feedback_and_one_explicit_send(outcome):
    calls = []
    step = 0

    async def call(name, arguments):
        calls.append(arguments)
        result = {"kind": outcome}
        capability.observe(result)
        return result

    base = await local_capability(
        key="inbound",
        model_alias="inbound",
        allowed=None,
        handler=call,
        tools=[Tool(name="reply", inputSchema={"type": "object"})],
    )
    assert base is not None
    capability = NativeReplyCapability(base)

    async def model(messages, info):
        nonlocal step
        step += 1
        if step == 1:
            yield "MEMORY-NOT-FOUND"
        elif step == 2:
            assert "has not been sent" in repr(messages)
            yield {0: DeltaToolCall(name="inbound_reply", json_args='{"text":"MEMORY-NOT-FOUND"}', tool_call_id="send")}
        else:
            yield "done"

    agent = HarnessBuilder().build(
        AgentSpec(), model=FunctionModel(stream_function=model), output_type=str, capabilities=(capability,)
    )
    result = await agent.run("reply")
    assert result.output_or_raise() == "done"
    assert calls == [{"text": "MEMORY-NOT-FOUND"}]


async def test_deliberate_silence_does_not_send():
    async def call(name, arguments):
        raise AssertionError("Silence must not publish a message")

    base = await local_capability(
        key="inbound",
        model_alias="inbound",
        allowed=None,
        handler=call,
        tools=[Tool(name="reply", inputSchema={"type": "object"})],
    )
    assert base is not None

    async def model(messages, info):
        yield " "

    capability = NativeReplyCapability(base)
    agent = HarnessBuilder().build(
        AgentSpec(), model=FunctionModel(stream_function=model), output_type=str, capabilities=(capability,)
    )
    result = await agent.run("observe silently")
    assert result.output_or_raise() == " "


async def test_recovered_attempt_does_not_repeat_recorded_reply():
    async def call(name, arguments):
        raise AssertionError("A recovered reply must not be sent again")

    async def recorded_reply():
        return True

    base = await local_capability(
        key="inbound",
        model_alias="inbound",
        allowed=None,
        handler=call,
        tools=[Tool(name="reply", inputSchema={"type": "object"})],
    )
    assert base is not None

    async def model(messages, info):
        yield "Already delivered"

    capability = NativeReplyCapability(base, recorded_reply=recorded_reply)
    agent = HarnessBuilder().build(
        AgentSpec(), model=FunctionModel(stream_function=model), output_type=str, capabilities=(capability,)
    )
    assert (await agent.run("resume")).output_or_raise() == "Already delivered"
