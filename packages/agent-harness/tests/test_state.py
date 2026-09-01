from typing import Any, cast

import pytest
from a13n_environment_provider import EnvironmentState
from a13n_harness import (
    HarnessRunResult,
    HarnessState,
    SafeFailure,
    StateError,
)
from a13n_harness.state import (
    AgentContextState,
    AgentContextStateSnapshot,
    CapabilityState,
)
from pydantic import BaseModel, ValidationError
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.usage import RunUsage

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_environment_states_reject_non_finite_json(value: float) -> None:
    with pytest.raises(ValueError, match="bounded finite JSON"):
        HarnessState.new(
            environment_states={
                "workspace": EnvironmentState(
                    provider_key="test.provider",
                    state_version="state-1",
                    state=value,
                )
            }
        )


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


def test_state_import_rejects_removed_process_namespace() -> None:
    with pytest.raises(ValidationError, match="no longer supported"):
        AgentContextStateSnapshot(
            entries={
                "a13n.dynamic-environment.processes": CapabilityState(
                    version="2",
                    data={"next_sequence": 2, "processes": {}},
                )
            }
        )


def test_thread_identity_is_stable_on_copy_and_rotates_on_fork() -> None:
    state = HarnessState.new()
    copied = state.model_copy(deep=True)
    restored = HarnessState.model_validate_json(state.model_dump_json())
    forked = state.fork()

    assert state.schema_version == "1"
    assert state.thread_id.startswith("thread-")
    assert copied.thread_id == state.thread_id
    assert restored.thread_id == state.thread_id
    assert forked.thread_id != state.thread_id
    assert forked.message_history == state.message_history
    assert forked.agent_context_state == state.agent_context_state
    assert forked.environment_states == {}


def test_independent_states_receive_distinct_thread_identities() -> None:
    assert HarnessState.new().thread_id != HarnessState.new().thread_id


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": "1", "message_history": []},
        {
            "thread_id": "thread-0123456789abcdef0123456789abcdef",
            "message_history": [],
        },
    ],
)
def test_import_requires_explicit_schema_version_and_thread_identity(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        HarnessState.model_validate(payload)


@pytest.mark.parametrize(
    "thread_id",
    [
        "",
        "thread-",
        "conversation-0123456789abcdef0123456789abcdef",
        "thread-0123456789abcdef0123456789abcde",
        "thread-0123456789abcdef0123456789abcdef0",
        "thread-0123456789ABCDEF0123456789ABCDEF",
    ],
)
def test_state_rejects_malformed_thread_identities(thread_id: str) -> None:
    with pytest.raises(ValidationError):
        HarnessState(schema_version="1", thread_id=thread_id)


async def test_state_and_result_views_do_not_expose_mutable_aliases() -> None:
    source_data: dict[str, Any] = {"items": [1]}
    capability = CapabilityState(version="1", data=source_data)
    cast(list[Any], source_data["items"]).append(2)
    assert capability.data == {"items": [1]}
    returned_data = cast(dict[str, Any], capability.data)
    cast(list[Any], returned_data["items"]).append(3)
    assert capability.data == {"items": [1]}

    message = ModelRequest(parts=[UserPromptPart(content="hello")])
    state = HarnessState.new(message_history=(message,))
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
            state=HarnessState.new(),
            usage=RunUsage(),
        )


async def test_result_rejects_inconsistent_terminal_fields() -> None:
    state = HarnessState.new()
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
