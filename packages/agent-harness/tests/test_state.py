from typing import Any, cast

import pytest
from converge_agent_harness import (
    AgentContextState,
    AgentContextStateSnapshot,
    CapabilityState,
    EnvironmentBindingState,
    EnvironmentState,
    HarnessRunResult,
    HarnessState,
    SafeFailure,
    StateError,
)
from pydantic import BaseModel, ValidationError
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.usage import RunUsage

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_environment_state_rejects_non_finite_json(value: float) -> None:
    environment = EnvironmentState(
        observed_topology_version=1,
        bindings={
            "binding-1": EnvironmentBindingState(
                provider_type="test.provider",
                state_version="state-1",
                resource_compatibility="portable",
                data=value,
            )
        },
    )
    with pytest.raises(ValueError, match="finite canonical JSON"):
        HarnessState(environment_state=environment)


class CounterState(BaseModel):
    value: int


async def test_capability_state_namespaces_are_typed_and_detached() -> None:
    state = AgentContextState()
    await state.write("counter", CounterState(value=1), version="1")

    value = await state.read("counter", CounterState, version="1")
    assert value == CounterState(value=1)

    snapshot = await state.snapshot()
    await state.write("counter", CounterState(value=2), version="1")

    assert snapshot.entries["counter"].data == {"value": 1}
    assert (await state.read("counter", CounterState, version="1")) == CounterState(value=2)


def test_thread_identity_is_stable_on_copy_and_rotates_on_fork() -> None:
    state = HarnessState()
    copied = state.model_copy(deep=True)
    restored = HarnessState.model_validate_json(state.model_dump_json())
    forked = state.fork()

    assert state.schema_version == "2"
    assert state.thread_id.startswith("thread-")
    assert copied.thread_id == state.thread_id
    assert restored.thread_id == state.thread_id
    assert forked.thread_id != state.thread_id
    assert forked.message_history == state.message_history
    assert forked.agent_context_state == state.agent_context_state
    assert forked.environment_state == state.environment_state


def test_independent_states_receive_distinct_thread_identities() -> None:
    assert HarnessState().thread_id != HarnessState().thread_id


def test_legacy_state_version_is_not_silently_imported() -> None:
    with pytest.raises(ValidationError):
        HarnessState.model_validate({"schema_version": "1", "message_history": []})


async def test_state_and_result_views_do_not_expose_mutable_aliases() -> None:
    source_data: dict[str, Any] = {"items": [1]}
    capability = CapabilityState(version="1", data=source_data)
    cast(list[Any], source_data["items"]).append(2)
    assert capability.data == {"items": [1]}
    returned_data = cast(dict[str, Any], capability.data)
    cast(list[Any], returned_data["items"]).append(3)
    assert capability.data == {"items": [1]}

    message = ModelRequest(parts=[UserPromptPart(content="hello")])
    state = HarnessState(message_history=(message,))
    message.parts.append(UserPromptPart(content="source mutation"))
    assert len(state.message_history[0].parts) == 1
    returned_messages = state.message_history
    returned_messages[0].parts.append(UserPromptPart(content="view mutation"))
    assert len(state.message_history[0].parts) == 1

    source_output = {"items": [1]}
    source_usage = RunUsage(requests=1, details={"cached": 2})
    cast(Any, source_usage).extension = {"nested": [1]}
    result = HarnessRunResult(
        thread_id=state.thread_id,
        run_id="run-1",
        status="completed",
        output=source_output,
        state=state,
        usage=source_usage,
        _messages=(message,),
    )
    source_output["items"].append(2)
    source_usage.requests = 9
    source_usage.details["cached"] = 9
    cast(Any, source_usage).extension["nested"].append(2)
    returned_output = result.output
    assert returned_output is not None
    returned_output["items"].append(3)
    returned_usage = result.usage
    returned_usage.details["cached"] = 4
    cast(Any, returned_usage).extension["nested"].append(3)

    assert result.output == {"items": [1]}
    assert result.usage.requests == 1
    assert result.usage.details == {"cached": 2}
    assert cast(Any, result.usage).extension == {"nested": [1]}
    assert len(result.all_messages()[0].parts) == 2
    returned_result_messages = result.all_messages()
    returned_result_messages[0].parts.append(UserPromptPart(content="result view mutation"))
    assert len(result.all_messages()[0].parts) == 2

    source_details: dict[str, Any] = {"items": [1]}
    failure = SafeFailure(code="failed", message="safe", details=source_details)
    cast(list[Any], source_details["items"]).append(2)
    returned_details = cast(dict[str, Any], failure.details)
    cast(list[Any], returned_details["items"]).append(3)
    assert failure.details == {"items": [1]}


async def test_result_rejects_mismatched_thread_state() -> None:
    with pytest.raises(ValueError, match=r"state\.thread_id"):
        HarnessRunResult(
            thread_id="thread-other",
            run_id="run-1",
            status="completed",
            output="done",
            state=HarnessState(),
            usage=RunUsage(),
        )


async def test_result_rejects_inconsistent_terminal_fields() -> None:
    state = HarnessState()
    with pytest.raises(ValueError, match="Invalid HarnessRunResult"):
        HarnessRunResult(
            thread_id=state.thread_id,
            run_id="run-1",
            status="suspended",
            output=None,
            state=state,
            usage=RunUsage(),
            suspend_reason="deferred",
        )


async def test_capability_state_rejects_an_unsupported_version() -> None:
    state = AgentContextState(
        AgentContextStateSnapshot(
            entries={"counter": CapabilityState(version="1", data={"value": 1})},
        )
    )

    try:
        await state.read("counter", CounterState, version="2")
    except StateError as exc:
        assert exc.code == "capability_state_version_unsupported"
    else:
        raise AssertionError("Expected an unsupported state version to fail.")
