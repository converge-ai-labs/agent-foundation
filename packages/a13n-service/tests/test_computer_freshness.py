"""Request-pinned Computer freshness through real Harness and owned CodeAct execution."""

import asyncio
import json
from datetime import UTC, datetime
from io import BytesIO

import pytest
from a13n_harness import HarnessBuilder, RunBindings
from a13n_harness.capabilities import CodeActCapability
from a13n_harness.capabilities.tool_execution import ToolExecutionCapability
from a13n_harness.environment.computer import ComputerCall, ComputerObservation
from a13n_harness.environment.models import EnvironmentAction, EnvironmentError
from a13n_harness.tool_execution import ToolExecutionConfig, ToolExecutionRuntime
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from a13n_service.runs.environments.desktop.freshness import Freshness, Invocation, Observation, current
from PIL import Image
from pydantic_ai import BinaryContent, ToolReturn
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

pytestmark = pytest.mark.anyio


def observation(episode=1, reference="frame"):
    return Observation(
        "control",
        1,
        episode,
        ComputerObservation(
            execution_id="environment",
            observed_generation="execution-generation",
            observation_id=reference,
            target_id="screen",
            width=100,
            height=100,
            mime_type="image/png",
            captured_at=datetime.now(UTC),
        ),
    )


def captured(source):
    image = BytesIO()
    Image.new("RGB", (100, 100)).save(image, format="PNG")
    native = BinaryContent(image.getvalue(), media_type="image/png", identifier="fresh-frame")
    source.observed(observation(), native.identifier)
    return ToolReturn(return_value="new screenshot", content=[native])


def check_input(source, *, episode=1, reference=None):
    source.require(
        "control",
        1,
        episode,
        ComputerCall(
            EnvironmentAction.COMPUTER_CLICK if reference else EnvironmentAction.COMPUTER_TYPE_TEXT,
            observation=reference,
        ),
    )


def test_freshness_is_immutable_target_episode_and_instance_specific():
    capability = Freshness()
    old = Invocation("agent", (), capability)
    payload = captured(old)
    observed = capability.observations["agent"]["fresh-frame"]
    messages = [ModelRequest(parts=[UserPromptPart(content=payload.content)])]
    with pytest.raises(EnvironmentError):
        check_input(old)
    new = capability.invocation("agent", messages)
    check_input(new, reference=observed.reference)
    with pytest.raises(EnvironmentError):
        check_input(new, episode=2)
    with pytest.raises(EnvironmentError):
        check_input(new, reference=observation(reference="old-frame").reference)
    sibling = capability.invocation("sibling", messages)
    with pytest.raises(EnvironmentError):
        check_input(sibling)
    with pytest.raises(EnvironmentError):
        check_input(Invocation("agent", (), Freshness()))


@pytest.mark.parametrize("mode", ["same_response", "delivered", "runner_failed", "invalid_image"])
async def test_observation_requires_a_subsequent_real_model_request(mode):
    outcomes = []
    requests = 0

    async def observe() -> ToolReturn:
        source = current.get()
        assert source is not None
        payload = captured(source)
        if mode == "invalid_image":
            payload.content = [BinaryContent(b"invalid", media_type="image/png", identifier="fresh-frame")]
        return payload

    async def click() -> str:
        source = current.get()
        assert source is not None
        try:
            check_input(source)
        except EnvironmentError:
            outcomes.append("blocked")
            return "blocked"
        outcomes.append("allowed")
        return "allowed"

    async def model(messages, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            code = "await observe()"
            if mode == "same_response":
                code += "\nawait click()"
            elif mode == "runner_failed":
                code += "\nraise ValueError('failed after capture')"
        elif requests == 2 and mode != "same_response":
            code = "await click()"
        else:
            yield "done"
            return
        yield {0: DeltaToolCall(name="run_code", tool_call_id=f"call-{requests}", json_args=json.dumps({"code": code}))}

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            Freshness(),
            CodeActCapability(),
            Capability(
                toolsets=[
                    CodeActPolicyToolset(
                        wrapped=FunctionToolset([observe, click], id="desktop-test"),
                        policy=CodeActToolPolicy(default=True),
                    )
                ]
            ),
        ),
    )
    result = await executable.run("test")
    assert result.status == "completed", result
    assert outcomes == (["allowed"] if mode == "delivered" else ["blocked"])


async def test_old_owned_runner_does_not_borrow_new_model_permission():
    entered, release = asyncio.Event(), asyncio.Event()
    requests, outcomes, receipts = 0, [], []

    async def observe() -> ToolReturn:
        source = current.get()
        assert source is not None
        return captured(source)

    async def delayed_click() -> str:
        source = current.get()
        assert source is not None
        entered.set()
        await release.wait()
        # Nested dispatch after the new request must still use this old immutable source.
        assert current.get() is source
        try:
            check_input(source)
        except EnvironmentError:
            outcomes.append("blocked")
            return "blocked"
        outcomes.append("allowed")
        return "allowed"

    async def click() -> str:
        source = current.get()
        assert source is not None
        check_input(source)
        release.set()
        return "allowed"

    async def model(messages, info):
        nonlocal requests
        requests += 1
        for message in messages:
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if (
                        isinstance(part, ToolReturnPart)
                        and isinstance(part.content, dict)
                        and "execution_id" in part.content
                    ):
                        receipts.append(part.content["execution_id"])
        if requests == 1:
            yield {0: DeltaToolCall(name="delayed_click", tool_call_id="old-click", json_args="{}")}
            return
        elif requests == 2:
            await asyncio.wait_for(entered.wait(), 2)
            yield {0: DeltaToolCall(name="observe", tool_call_id="fresh-observe", json_args="{}")}
            return
        elif requests == 3:
            yield {0: DeltaToolCall(name="click", tool_call_id="fresh-click", json_args="{}")}
            return
        else:
            yield "done"
            return

    runtime = ToolExecutionRuntime()
    # A non-sequential owned tool allows the next request to observe concurrently.
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            Freshness(),
            ToolExecutionCapability(ToolExecutionConfig(mode="auto", foreground_wait_seconds=0.05)),
            Capability(
                toolsets=[
                    CodeActPolicyToolset(
                        wrapped=FunctionToolset(
                            [observe, delayed_click, click],
                            id="desktop-test",
                            metadata={"a13n.tool-execution": "owned"},
                        ),
                        policy=CodeActToolPolicy(default=True),
                    )
                ]
            ),
        ),
    )
    try:
        result = await asyncio.wait_for(
            executable.run("test", bindings=RunBindings.embedded(tool_execution=runtime)), 10
        )
        assert result.status == "completed", result
        assert receipts
        release.set()
        outcome = await runtime.wait(result.thread_id, receipts[0])
        assert outcomes == ["blocked"]
        assert outcome.record.status == "succeeded"
    finally:
        release.set()
        await runtime.close()


@pytest.mark.parametrize("surface", ["user", "tool", "nested_json", "segment", "summary"])
def test_only_native_whole_images_in_the_actual_request_authorize_input(surface):
    capability = Freshness()
    payload = captured(Invocation("agent", (), capability))
    image = payload.content[0]
    if surface == "user":
        part = UserPromptPart(content=[image])
    elif surface == "tool":
        part = ToolReturnPart(tool_name="observe", tool_call_id="observe", content=[image])
    elif surface == "nested_json":
        part = ToolReturnPart(tool_name="observe", tool_call_id="observe", content={"image": image})
    elif surface == "segment":
        part = UserPromptPart(
            content=[BinaryContent(image.data, media_type="image/png", identifier="fresh-frame-segment-1")]
        )
    else:
        part = UserPromptPart(content="Summary: observed fresh-frame and the desktop is ready.")
    invocation = capability.invocation("agent", [ModelRequest(parts=[part])])
    if surface in {"user", "tool"}:
        check_input(invocation)
    else:
        with pytest.raises(EnvironmentError):
            check_input(invocation)
    # A later compacted request cannot borrow permission from an earlier request.
    with pytest.raises(EnvironmentError):
        check_input(capability.invocation("agent", []))


def test_retained_payload_preserves_capture_identity():
    from a13n_service.runs.tool_executions.payload import Payload

    capability = Freshness()
    payload = captured(Invocation("agent", (), capability))
    restored = Payload.model_validate_json(Payload(wrapped=True, result=payload, artifacts=[]).model_dump_json())
    check_input(capability.invocation("agent", [ModelRequest(parts=[UserPromptPart(content=restored.result.content)])]))


async def test_owned_capture_completion_requires_result_retrieval_and_a_new_request():
    captured_event, finish = asyncio.Event(), asyncio.Event()
    runtime = ToolExecutionRuntime()
    requests, receipts, outcomes = 0, [], []

    async def observe() -> ToolReturn:
        source = current.get()
        assert source is not None
        payload = captured(source)
        captured_event.set()
        await finish.wait()
        return payload

    async def click() -> str:
        source = current.get()
        assert source is not None
        try:
            check_input(source)
            outcomes.append("allowed")
        except EnvironmentError:
            outcomes.append("blocked")
        finish.set()
        return outcomes[-1]

    async def model(messages, info):
        nonlocal requests
        requests += 1
        for message in messages:
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if (
                        isinstance(part, ToolReturnPart)
                        and isinstance(part.content, dict)
                        and "execution_id" in part.content
                    ):
                        receipts.append(part.content["execution_id"])
        if requests == 1:
            name, args = "observe", {}
        elif requests == 2:
            await asyncio.wait_for(captured_event.wait(), 2)
            name, args = "click", {}
        elif requests == 3:
            assert outcomes == ["blocked"]
            name, args = "tool_execution_wait", {"execution_id": receipts[0], "timeout_seconds": 2}
        elif requests == 4:
            name, args = "click", {}
        else:
            yield "done"
            return
        yield {0: DeltaToolCall(name=name, tool_call_id=f"call-{requests}", json_args=json.dumps(args))}

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            Freshness(),
            ToolExecutionCapability(ToolExecutionConfig(mode="auto", foreground_wait_seconds=0.05)),
            Capability(toolsets=[FunctionToolset([observe, click], metadata={"a13n.tool-execution": "owned"})]),
        ),
    )
    try:
        result = await asyncio.wait_for(
            executable.run("test", bindings=RunBindings.embedded(tool_execution=runtime)), 10
        )
        assert result.status == "completed", result
        assert outcomes == ["blocked", "allowed"]
    finally:
        finish.set()
        await runtime.close()
