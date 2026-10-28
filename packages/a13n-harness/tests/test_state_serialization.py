"""Complete state persistence must preserve native messages and reject invalid history."""

import json
import warnings
from typing import Any, cast

import pytest
from a13n_harness import HarnessState
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.state import AgentContextStateSnapshot, CapabilityState, StoredRef
from pydantic import model_serializer
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_core import PydanticSerializationError


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [ModelRequest(parts=[UserPromptPart('中文🙂 " \\ \n')])],
        [ModelRequest(parts=[UserPromptPart([BinaryContent(data=b"\x00\xff\x80", media_type="image/png")])])],
        [
            ModelResponse(parts=[ToolCallPart("lookup", {"z": [1, None], "a": {"value": True}}, tool_call_id="c1")]),
            ModelRequest(parts=[ToolReturnPart("lookup", {"value": "result"}, tool_call_id="c1")]),
        ],
        [
            ModelResponse(
                parts=[ThinkingPart("thinking", signature="opaque", provider_name="anthropic"), TextPart("ok")]
            )
        ],
    ],
    ids=["empty", "unicode", "binary", "tool", "thinking"],
)
def test_complete_json_bytes_preserve_native_state(messages: Any) -> None:
    state = HarnessState(
        schema_version="1",
        thread_id="thr_serialization",
        message_history=messages,
        agent_context_state=AgentContextStateSnapshot(
            entries={"future.plugin": CapabilityState(version="2", data={"nested": [1, "value"]})}
        ),
        environment_states={"work": EnvironmentState(provider_key="test", state_version="1", state={"cursor": 3})},
        refs=(StoredRef(key="contents/example", digest="a" * 64, size=42),),
        future_envelope={"items": [False, None, {"value": "preserve"}]},
    )
    encoded = state.to_json_bytes()
    assert json.loads(encoded) == json.loads(state.model_dump_json())
    restored = HarnessState.model_validate_json(encoded)
    assert restored == HarnessState.model_validate_json(state.model_dump_json())
    assert restored.message_history == state.message_history
    assert restored.refs == state.refs
    assert restored.model_extra == state.model_extra


def test_serialization_keeps_input_and_output_messages_detached() -> None:
    args: dict[str, Any] = {"z": [1], "a": {"value": "original"}}
    message = ModelResponse(parts=[ToolCallPart("lookup", args, tool_call_id="c1")])
    state = HarnessState.new(message_history=[message])
    before = state.to_json_bytes()
    args["z"].append(2)
    message.parts.append(TextPart("changed"))
    detached = cast(ModelResponse, state.message_history[0])
    cast(dict[str, Any], cast(ToolCallPart, detached.parts[0]).args)["z"].append(3)
    assert state.to_json_bytes() == before
    restored = HarnessState.model_validate_json(before)
    saved_args = cast(ToolCallPart, cast(ModelResponse, restored.message_history[0]).parts[0]).args
    assert saved_args == {"z": [1], "a": {"value": "original"}}
    assert list(cast(dict[str, Any], saved_args)) == ["z", "a"]
    assert "message_history" not in state.model_dump(exclude={"message_history"})


@pytest.mark.parametrize("part_kind", ["user", "tool"])
def test_invalid_mutated_native_dataclasses_still_fail_serialization(part_kind: str) -> None:
    if part_kind == "user":
        part = UserPromptPart("valid")
        cast(Any, part).content = 123
        message = ModelRequest(parts=[part])
    else:
        tool = ToolCallPart("lookup", {"value": 1}, tool_call_id="c1")
        cast(Any, tool).args = 123
        message = ModelResponse(parts=[tool])
    # Native dataclass instances can pass validate_python even after invalid mutation.
    with warnings.catch_warnings(record=True):
        state = HarnessState.new(message_history=[message])
    for serialize in (state.model_dump_json, state.to_json_bytes):
        with pytest.raises(PydanticSerializationError):
            serialize()


@pytest.mark.parametrize("history", [b"not json", b"null", b"{}", b"[1]", b"\xff", b"[] trailing", ["invalid"]])
def test_unchecked_history_cannot_bypass_validation(history: Any) -> None:
    state = HarnessState.new().model_copy(update={"message_history_json": history})
    for serialize in (state.model_dump_json, state.to_json_bytes):
        with pytest.raises(PydanticSerializationError):
            serialize()


def test_subclass_retains_its_custom_serializer() -> None:
    class CustomState(HarnessState):
        @model_serializer
        def custom(self) -> dict[str, str]:
            return {"custom": self.thread_id}

    state = CustomState.new(thread_id="thr_custom")
    assert state.to_json_bytes() == state.model_dump_json().encode()
    assert json.loads(state.to_json_bytes()) == {"custom": "thr_custom"}
