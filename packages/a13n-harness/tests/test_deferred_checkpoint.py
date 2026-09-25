"""Native deferred facts survive a pre-dispatch checkpoint without replay grants."""

import asyncio
import json
from dataclasses import dataclass, field, replace

import pytest
from a13n_harness import DeferredToolResume, HarnessBuilder, HarnessEvent, HarnessState
from a13n_harness.tools.client import (
    ClientToolDefinition,
    ClientToolsCapability,
    ClientToolsetDefinition,
    ClientToolsSpec,
)
from a13n_harness.tools.metadata import RECOVERY_RETRY_SAFE_METADATA_KEY
from pydantic_ai import Tool, ToolApproved, ToolFailed, ToolReturn
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability, Capability
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.tools import DeferredToolResults

pytestmark = pytest.mark.anyio


@dataclass
class Capture(AbstractCapability):
    id: str = "test-checkpoint"
    states: list[HarnessState] = field(default_factory=list)
    cut: bool = False
    started: asyncio.Event = field(default_factory=asyncio.Event)

    async def before_tool_execute(self, ctx, *, call, tool_def, args):
        self.states.append(await ctx.deps.export_state(ctx.messages))
        if self.cut:
            self.started.set()
            await asyncio.Event().wait()
        return args


async def mixed_model(messages, info):
    if any(
        isinstance(message, ModelRequest) and any(isinstance(part, ToolReturnPart) for part in message.parts)
        for message in messages
    ):
        yield "done"
    else:
        yield {
            0: DeltaToolCall(name="change", json_args="{}", tool_call_id="call_change"),
            1: DeltaToolCall(name="client_result", json_args="{}", tool_call_id="call_client"),
        }


def executable(capture, effects, *, interrupt_after=False, safe=False):
    async def change():
        effects.append("changed")
        if interrupt_after:
            capture.started.set()
            await asyncio.Event().wait()
        return "changed"

    return HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=mixed_model),
        capabilities=(
            capture,
            Capability(
                tools=[Tool(change, requires_approval=True, metadata={RECOVERY_RETRY_SAFE_METADATA_KEY: safe})],
                id="change-tools",
            ),
            ClientToolsCapability(
                spec=ClientToolsSpec(
                    default_toolsets=(
                        ClientToolsetDefinition(
                            toolset_id="client",
                            tools=(
                                ClientToolDefinition(
                                    name="client_result",
                                    description="Provide the external review.",
                                    parameters_json_schema={"type": "object", "properties": {}},
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


# Every cut, recovery mode, and retry-safety combination decides replay; the checkpoint kind and the
# client result form each meet every value of the other dimensions at least once, and a returned
# ToolReturn meets both checkpoint kinds under both cuts because each holds a different prefix.
@pytest.mark.parametrize(
    ("cut", "mode", "safe", "checkpoint_kind", "supplied"),
    [
        ("before", "declared", False, "boundary", "json"),
        ("before", "declared", True, "terminal", "failed"),
        ("before", "never", False, "terminal", "return"),
        ("before", "never", True, "boundary", "json"),
        ("after", "declared", False, "terminal", "failed"),
        ("after", "declared", True, "boundary", "return"),
        ("after", "never", False, "boundary", "failed"),
        ("after", "never", True, "terminal", "json"),
        ("before", "declared", True, "boundary", "return"),
        ("after", "never", False, "terminal", "return"),
    ],
)
async def test_mixed_checkpoint_preserves_client_fact_without_replaying_unsafe_approval(
    cut, mode, safe, supplied, checkpoint_kind
):
    effects = []
    capture = Capture(cut=cut == "before")
    agent = executable(capture, effects, interrupt_after=cut == "after")
    first = await agent.run("go")
    assert first.deferred is not None and first.state is not None
    assert not effects
    client_result = {
        "json": {"review": "provided once"},
        "failed": ToolFailed("provided failure once"),
        "return": ToolReturn(
            {"review": "provided once"}, content="external explanation", metadata={"source": "client"}
        ),
    }[supplied]
    accepted = DeferredToolResume(
        first.deferred,
        DeferredToolResults(calls={"call_client": client_result}, approvals={"call_change": ToolApproved()}),
    )
    async with agent.stream(previous_state=first.state, deferred_resume=accepted) as run_stream:

        async def drain():
            async for item in run_stream:
                if not isinstance(item, HarnessEvent):
                    return item
            raise AssertionError("No terminal result")

        pending = asyncio.create_task(drain())
        await asyncio.wait_for(capture.started.wait(), timeout=5)
        run_stream.cancel()
        terminal = await asyncio.wait_for(pending, timeout=5)
        assert terminal.result.status == "cancelled"
    checkpoint = capture.states[-1] if checkpoint_kind == "boundary" else terminal.result.state
    assert checkpoint is not None
    assert len(effects) == (cut == "after")
    restored = HarnessState.model_validate_json(checkpoint.model_dump_json())
    recovery = executable(Capture(), effects, safe=safe)
    assert "a13n.tool-execution-boundary.deferred-results" not in restored.agent_context_state.entries
    result = await recovery.run(
        previous_state=restored, tool_recovery=mode, deferred_resume=replace(accepted, recovery=True)
    )
    if safe and mode == "declared":
        assert result.status == "suspended"
        assert result.deferred is not None and result.state is not None
        assert not result.deferred.calls
        assert [call.tool_call_id for call in result.deferred.approvals] == ["call_change"]
        result = await recovery.run(
            previous_state=HarnessState.model_validate_json(result.state.model_dump_json()),
            deferred_resume=DeferredToolResume(result.deferred, DeferredToolResults(approvals={"call_change": False})),
        )
    assert result.status == "completed"
    assert len(effects) == (cut == "after")
    assert result.state is not None
    returns = [
        part
        for message in result.state.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    client = [part for part in returns if part.tool_call_id == "call_client"]
    assert len(client) == 1
    if supplied == "failed":
        assert client[0].content == "provided failure once" and client[0].outcome == "failed"
    else:
        assert client[0].content == {"review": "provided once"} and client[0].outcome == "success"
    if supplied == "return":
        assert client[0].metadata == {"source": "client"}
        content = [
            part.content
            for message in result.state.message_history
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
        ]
        assert content.count("external explanation") == 1
    local = [part for part in returns if part.tool_call_id == "call_change"]
    assert len(local) == 1 and local[0].outcome == ("denied" if safe and mode == "declared" else "failed")
    assert "provided" in json.dumps(result.state.model_dump(mode="json"))


async def test_accepted_recovery_input_is_detached_and_rejects_changed_correlation():
    from a13n_harness.errors import RunError
    from a13n_harness.tools.deferred import preflight_deferred_resume
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    first = await executable(Capture(), []).run("go")
    assert first.deferred is not None and first.state is not None
    original = {"review": ["original"]}
    accepted = DeferredToolResume(
        first.deferred,
        DeferredToolResults(calls={"call_client": original}, approvals={"call_change": True}),
        recovery=True,
    )
    original["review"].append("mutated")
    assert accepted.results.calls["call_client"] == {"review": ["original"]}
    messages = first.state.message_history
    response = next(message for message in reversed(messages) if isinstance(message, ModelResponse))
    next(part for part in response.parts if isinstance(part, ToolCallPart)).tool_name = "substituted"
    changed = HarnessState.new(thread_id=first.state.thread_id, message_history=messages)
    with pytest.raises(RunError, match="does not match"):
        preflight_deferred_resume(accepted, previous_state=changed)


async def test_recovery_omits_results_already_incorporated_in_history():
    first = await executable(Capture(), []).run("go")
    assert first.deferred is not None and first.state is not None
    accepted = DeferredToolResume(
        first.deferred,
        DeferredToolResults(calls={"call_client": "accepted"}, approvals={"call_change": False}),
        recovery=True,
    )
    partial = HarnessState.new(
        thread_id=first.state.thread_id,
        message_history=(
            *first.state.message_history,
            ModelRequest(parts=[ToolReturnPart("client_result", "accepted", "call_client")]),
        ),
    )
    remaining = accepted.remaining(partial.message_history)
    assert remaining is not None and not remaining.results.calls
    result = await executable(Capture(), []).run(previous_state=partial, deferred_resume=accepted)
    assert result.state is not None
    assert accepted.remaining(result.state.message_history) is None
    returns = [
        part
        for message in result.state.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_call_id == "call_client"
    ]
    assert len(returns) == 1 and returns[0].content == "accepted"
