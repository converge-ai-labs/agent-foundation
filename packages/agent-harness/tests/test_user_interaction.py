from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    DeferredToolResume,
    HarnessBuilder,
    RunBindings,
    RunError,
    UserInteractionCapability,
)
from a13n_harness.environment.advanced import NoopEnvironmentRunBinding
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio

_QUESTIONS = {
    "questions": [
        {
            "header": "Scope",
            "question": "Which scope should be used?",
            "options": [
                {"label": "Focused", "description": "Change only the selected component."},
                {"label": "Broad", "description": "Apply the change across the repository."},
            ],
            "multiSelect": False,
        }
    ]
}


def _build() -> object:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="ask_user_question",
                    json_args=json.dumps(_QUESTIONS),
                    tool_call_id="question-1",
                )
            }
        else:
            yield f"answer:{returns[-1].content}"

    return HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(UserInteractionCapability(),),
    )


async def test_structured_question_suspends_and_resumes_through_native_deferred_values() -> None:
    executable = _build()
    first = await executable.run("clarify", bindings=RunBindings.embedded())

    assert first.status == "suspended"
    assert first.state is not None and first.deferred is not None
    assert [call.tool_name for call in first.deferred.calls] == ["ask_user_question"]
    call_id = first.deferred.calls[0].tool_call_id

    second = await _build().run(
        bindings=RunBindings.embedded(),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(
            first.deferred,
            first.deferred.build_results(
                calls={
                    call_id: {
                        "answers": {"Which scope should be used?": "Focused"},
                    }
                }
            ),
        ),
    )

    assert second.status == "completed"
    assert "Focused" in second.output_or_raise()


async def test_structured_question_is_not_exposed_to_child_runs() -> None:
    observed_tools: list[set[str]] = []
    observed_instructions: list[str] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        observed_tools.append({tool.name for tool in info.function_tools})
        observed_instructions.append(info.instructions or "")
        yield "child-done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(UserInteractionCapability(),),
    )
    result = await executable.run(
        "clarify",
        bindings=RunBindings(
            instance=AgentInstanceContext(
                identity=AgentIdentityRef(issuer="test", subject="child"),
                agent_instance_id="child-1",
                parent_agent_instance_id="parent-1",
                delegation_id="delegation-1",
            ),
            environment=NoopEnvironmentRunBinding(),
        ),
    )

    assert result.output_or_raise() == "child-done"
    assert observed_tools == [set()]
    assert "ask_user_question" not in observed_instructions[0]


async def test_structured_question_rejects_uncorrelated_answer_shape_before_resume() -> None:
    first = await _build().run("clarify", bindings=RunBindings.embedded())
    assert first.state is not None and first.deferred is not None
    call_id = first.deferred.calls[0].tool_call_id

    with pytest.raises(RunError) as invalid:
        await _build().run(
            bindings=RunBindings.embedded(),
            previous_state=first.state,
            deferred_resume=DeferredToolResume(
                first.deferred,
                first.deferred.build_results(calls={call_id: {"answers": {"Another question": "Focused"}}}),
            ),
        )

    assert invalid.value.code == "deferred_results_invalid"
