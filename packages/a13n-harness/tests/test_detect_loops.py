from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from a13n_harness import AgentContext, AgentSpec, DefinitionError, HarnessBuilder, RunBindings
from pydantic_ai.capabilities import AbstractCapability, Capability
from pydantic_ai.messages import ModelMessage, SystemPromptPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai_harness import DetectLoops

pytestmark = pytest.mark.anyio


def _spec(**arguments: object) -> AgentSpec:
    return AgentSpec.model_validate({"capabilities": [{"DetectLoops": arguments}]})


def test_detect_loops_schema_uses_native_configuration_without_runtime_state() -> None:
    schema = AgentSpec.model_json_schema_with_capabilities()
    parameters = schema["$defs"]["spec_params_DetectLoops"]
    assert parameters["properties"]["action"]["enum"] == ["nudge", "error"]
    assert not any(name.startswith("_") for name in parameters["properties"])
    assert parameters["additionalProperties"] is False
    spec = _spec(action="error", repeated_result_threshold=None)
    assert AgentSpec.model_validate_json(spec.model_dump_json()) == spec


@pytest.mark.parametrize("enabled", [False, True])
def test_detect_loops_is_available_but_opt_in(enabled: bool) -> None:
    executable = HarnessBuilder().build(_spec() if enabled else AgentSpec(), output_type=str)
    leaves: list[AbstractCapability[AgentContext]] = []
    executable._agent.root_capability.apply(leaves.append)
    assert sum(type(item) is DetectLoops for item in leaves) == int(enabled)


@pytest.mark.parametrize("arguments", [{"window": 1}, {"repeated_call_threshold": 11}, {"unknown": True}])
def test_detect_loops_rejects_invalid_constructor_configuration(arguments: dict[str, object]) -> None:
    with pytest.raises(DefinitionError) as error:
        HarnessBuilder().build(_spec(**arguments), output_type=str)
    assert error.value.code == "agent_build_failed"


@pytest.mark.parametrize("action", ["nudge", "error"])
async def test_detect_loops_repeated_results_reach_model_or_end_run(action: str) -> None:
    requests = 0
    calls = 0
    model_nudges: list[str] = []

    def poll() -> str:
        nonlocal calls
        calls += 1
        return "pending"

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests
        del info
        requests += 1
        model_nudges.extend(
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, UserPromptPart)
            and isinstance(part.content, str)
            and "Your last 3 calls to `poll`" in part.content
        )
        if requests <= 3:
            yield {0: DeltaToolCall(name="poll", json_args="{}", tool_call_id=f"poll-{requests}")}
        else:
            yield "Polling is not making progress."

    executable = HarnessBuilder().build(
        _spec(action=action),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(tools=[poll]),),
    )
    result = await executable.run("Check status", bindings=RunBindings.embedded())
    assert calls == 3
    if action == "nudge":
        assert result.output_or_raise() == "Polling is not making progress."
        assert requests == 4
        assert len(model_nudges) == 1
        nudges = [
            part
            for message in result.all_messages()
            for part in message.parts
            if isinstance(part, SystemPromptPart)
            and part.metadata.get("origin") == "pydantic-ai-harness.detect_loops.nudge.v1"
        ]
        assert len(nudges) == 1
        assert nudges[0].metadata["pattern"] == "repeated_result"
    else:
        assert result.status == "failed"
        assert result.failure is not None
        assert result.failure.code == "agent_run_failed"
        assert result.failure.details["exception_type"] == "LoopDetectedError"
        assert result.failure.retry_hint == "dependency_change"
        assert requests == 3
        assert not model_nudges


async def test_detect_loops_does_not_seed_counts_from_continuation_history() -> None:
    requests = 0

    def poll() -> str:
        return "pending"

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests
        del messages, info
        requests += 1
        if requests % 3:
            yield {0: DeltaToolCall(name="poll", json_args="{}", tool_call_id=f"poll-{requests}")}
        else:
            yield "Still pending."

    executable = HarnessBuilder().build(
        _spec(action="error"),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(tools=[poll]),),
    )
    first = await executable.run("Check status", bindings=RunBindings.embedded())
    assert first.output_or_raise() == "Still pending."
    second = await executable.run("Check again", previous_state=first.state, bindings=RunBindings.embedded())
    assert second.output_or_raise() == "Still pending."
    assert requests == 6


async def test_detect_loops_can_disable_call_checks_for_expected_polling() -> None:
    requests = 0

    def poll() -> str:
        return "pending"

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests
        del messages, info
        requests += 1
        if requests <= 6:
            yield {0: DeltaToolCall(name="poll", json_args="{}", tool_call_id=f"poll-{requests}")}
        else:
            yield "done"

    executable = HarnessBuilder().build(
        _spec(action="error", repeated_call_threshold=None, repeated_result_threshold=None),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(tools=[poll]),),
    )
    result = await executable.run("Poll until ready", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    assert requests == 7
