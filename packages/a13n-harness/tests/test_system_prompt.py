from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy

import pytest
from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
    RunBindings,
)
from a13n_harness._run_stream import _reconcile_system_prompt
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

pytestmark = pytest.mark.anyio


def _system_prompt_contents(messages: list[ModelMessage] | tuple[ModelMessage, ...]) -> list[str]:
    return [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, SystemPromptPart)
    ]


def test_harness_agent_spec_schema_includes_system_prompt() -> None:
    schema = AgentSpec.model_json_schema_with_capabilities()

    assert schema["properties"]["system_prompt"] == {
        "anyOf": [
            {"type": "string"},
            {"items": {"type": "string"}, "type": "array"},
            {"type": "null"},
        ],
        "default": None,
    }
    assert schema["properties"]["toolset_instructions"] == {
        "type": "boolean",
        "default": True,
    }
    assert AgentSpec(system_prompt=["first", "second"]).model_dump()["system_prompt"] == [
        "first",
        "second",
    ]


async def test_system_prompt_is_injected_for_initial_history_and_replaced_on_resume() -> None:
    observed: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        observed.append(deepcopy(messages))
        yield "done"

    model = FunctionModel(stream_function=stream)
    original = HarnessBuilder().build(
        AgentSpec(system_prompt=["original one", "original two"]),
        output_type=str,
        model=model,
    )
    retained_original_prompt = original.definition.agent.system_prompt
    assert isinstance(retained_original_prompt, list)
    retained_original_prompt.append("mutated after build")
    first = await original.run("start", bindings=RunBindings.embedded())
    assert first.state is not None

    revised = HarnessBuilder().build(
        AgentSpec(system_prompt=["revised one", "revised two"]),
        output_type=str,
        model=model,
    )
    retained_revised_prompt = revised.definition.agent.system_prompt
    assert isinstance(retained_revised_prompt, list)
    retained_revised_prompt.append("mutated after build")
    second = await revised.run(
        "continue",
        bindings=RunBindings.embedded(),
        previous_state=first.state,
    )

    assert _system_prompt_contents(observed[0]) == ["original one", "original two"]
    assert _system_prompt_contents(observed[1]) == ["revised one", "revised two"]
    assert _system_prompt_contents(second.all_messages()) == ["revised one", "revised two"]
    first_request = next(message for message in observed[1] if isinstance(message, ModelRequest))
    assert [type(part) for part in first_request.parts[:2]] == [SystemPromptPart, SystemPromptPart]


async def test_empty_system_prompt_removes_historical_blocks_on_resume() -> None:
    observed: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        observed.append(deepcopy(messages))
        yield "done"

    model = FunctionModel(stream_function=stream)
    original = HarnessBuilder().build(
        AgentSpec(system_prompt="remove me"),
        output_type=str,
        model=model,
    )
    first = await original.run("start", bindings=RunBindings.embedded())
    assert first.state is not None

    revised = HarnessBuilder().build(
        AgentSpec(system_prompt=None),
        output_type=str,
        model=model,
    )
    second = await revised.run(
        "continue",
        bindings=RunBindings.embedded(),
        previous_state=first.state,
    )

    assert _system_prompt_contents(observed[0]) == ["remove me"]
    assert _system_prompt_contents(observed[1]) == []
    assert _system_prompt_contents(second.all_messages()) == []


def test_reconciliation_preserves_request_fields_and_skips_provider_suspension() -> None:
    first = ModelRequest(
        parts=[SystemPromptPart("old"), UserPromptPart("start")],
        instructions="keep instructions",
        run_id="run-original",
        conversation_id="thread-original",
        metadata={"owner": "test"},
    )
    later = ModelRequest(parts=[SystemPromptPart("historical reminder"), UserPromptPart("continue")])
    complete = ModelResponse(parts=[TextPart("complete")])

    reconciled = _reconcile_system_prompt((first, complete, later), ("new one", "new two"))

    assert _system_prompt_contents(reconciled) == ["new one", "new two", "historical reminder"]
    assert reconciled[2] is later
    reconciled_first = reconciled[0]
    assert isinstance(reconciled_first, ModelRequest)
    assert reconciled_first.instructions == "keep instructions"
    assert reconciled_first.run_id == "run-original"
    assert reconciled_first.conversation_id == "thread-original"
    assert reconciled_first.metadata == {
        "owner": "test",
        "a13n.content": {"2": [{"display": True, "source_id": None, "media": False}]},
    }
    assert isinstance(reconciled[1], ModelResponse)

    suspended = ModelResponse(parts=[TextPart("partial")], state="suspended")
    unchanged = _reconcile_system_prompt((first, suspended), ("new",))

    assert unchanged == (first, suspended)
    assert _system_prompt_contents(unchanged) == ["old"]


@pytest.mark.parametrize("current_prompt", [None, "new definition"])
async def test_resume_preserves_scoped_and_identified_historical_prompts(current_prompt: str | None) -> None:
    from a13n_harness import HarnessState

    identified = SystemPromptPart("catalog note", metadata={"capability": "catalog"})
    request_only = SystemPromptPart("old request budget", scope="request")
    dynamic = SystemPromptPart("dynamic note", dynamic_ref="custom_prompt")
    reminder = SystemPromptPart("choose a different approach")
    history = [
        ModelRequest(parts=[SystemPromptPart("old definition"), identified, UserPromptPart("start"), request_only]),
        ModelResponse(parts=[TextPart("working")]),
        ModelRequest(parts=[dynamic, reminder, UserPromptPart("continue")]),
        ModelResponse(parts=[TextPart("done")]),
    ]
    saved = HarnessState.new(message_history=history)
    restored = HarnessState.model_validate_json(saved.to_json_bytes())
    observed: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        observed.append(deepcopy(messages))
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(system_prompt=current_prompt),
        model=FunctionModel(stream_function=stream, profile={"supports_inline_system_prompts": True}),
        output_type=str,
    )
    result = await executable.run("next", previous_state=restored)
    expected = ([current_prompt] if current_prompt else []) + [
        identified.content,
        request_only.content,
        dynamic.content,
        reminder.content,
    ]
    # The adapter expires request-scoped prompts without deleting them from saved history.
    assert _system_prompt_contents(observed[0]) == [text for text in expected if text != request_only.content]
    assert _system_prompt_contents(result.all_messages()) == expected
    retained = [part for msg in result.all_messages() if isinstance(msg, ModelRequest) for part in msg.parts]
    for part in (identified, request_only, dynamic, reminder):
        assert part in retained
    assert saved.to_json_bytes() == restored.to_json_bytes()


@pytest.mark.parametrize(
    "prompt",
    [
        SystemPromptPart("identified", metadata={"owner": "custom"}),
        SystemPromptPart("request", scope="request"),
        SystemPromptPart("dynamic", dynamic_ref="custom"),
    ],
)
def test_reconciliation_keeps_non_definition_prompt_at_start(prompt: SystemPromptPart) -> None:
    history = (ModelRequest(parts=[prompt, UserPromptPart("start")]),)
    reconciled = _reconcile_system_prompt(history, ("definition",))
    assert _system_prompt_contents(reconciled) == ["definition", prompt.content]
    assert isinstance(reconciled[0], ModelRequest)
    assert reconciled[0].parts[1] is prompt
