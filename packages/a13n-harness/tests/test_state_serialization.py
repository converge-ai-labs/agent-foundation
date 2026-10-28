"""State persistence reuses encoded history; external imports validate native messages."""

import json
import warnings
from typing import Any, cast

import pytest
from a13n_harness import HarnessState
from a13n_harness import state as state_module
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.state import AgentContextStateSnapshot, CapabilityState, StoredRef
from pydantic import ValidationError, model_serializer
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


def test_reading_decodes_once_and_serialization_reuses_history_bytes(monkeypatch) -> None:
    state = HarnessState.new(message_history=[ModelResponse(parts=[TextPart("original")])])
    expected = state.to_json_bytes()
    decoded = []
    original = state_module.decode_messages

    def decode(value):
        decoded.append(value)
        return original(value)

    def encode(value):
        pytest.fail("Serialization must reuse the encoded history")

    monkeypatch.setattr(state_module, "decode_messages", decode)
    monkeypatch.setattr(state_module, "encode_messages", encode)
    messages = state.message_history
    cast(TextPart, messages[0].parts[0]).content = "changed"
    assert decoded == [state.message_history_json]
    assert state.to_json_bytes() == expected
    assert state.to_json_bytes() == expected
    assert len(decoded) == 1
    assert state.message_history[0].parts[0].content == "original"
    assert len(decoded) == 2


def test_serialization_uses_current_history_and_envelope() -> None:
    state = HarnessState.new(message_history=[ModelResponse(parts=[TextPart("original")])])
    before = state.to_json_bytes()
    replacement = HarnessState.new(message_history=[ModelResponse(parts=[TextPart("replacement")])])
    copied = state.model_copy(
        update={"thread_id": "thr_other", "message_history_json": replacement.message_history_json}
    )
    encoded = copied.to_json_bytes()
    assert copied.message_history == replacement.message_history
    assert json.loads(encoded) == json.loads(copied.model_dump_json())
    assert json.loads(encoded)["thread_id"] == "thr_other"
    assert state.to_json_bytes() == before


def test_other_fields_keep_standard_serialization() -> None:
    invalid = HarnessState.new().model_copy(update={"environment_states_json": b"[1]"})
    with pytest.raises(PydanticSerializationError):
        invalid.to_json_bytes()


@pytest.mark.parametrize("part_kind", ["user", "tool"])
def test_standard_pydantic_serialization_still_rejects_invalid_mutated_dataclasses(part_kind: str) -> None:
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
    with pytest.raises(PydanticSerializationError):
        state.model_dump_json()


@pytest.mark.parametrize(
    "history",
    [
        None,
        {},
        [1],
        [{"kind": "request", "parts": [{"part_kind": "user-prompt", "content": 123}]}],
        [
            {
                "kind": "response",
                "parts": [{"part_kind": "tool-call", "tool_name": "lookup", "args": 123, "tool_call_id": "c1"}],
            }
        ],
    ],
)
def test_external_history_is_validated_on_import(history: Any) -> None:
    payload = json.loads(HarnessState.new().to_json_bytes())
    payload["message_history"] = history
    with pytest.raises(ValidationError):
        HarnessState.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("data", [b"not json", b"\xff", b"{} trailing"])
def test_malformed_external_json_is_rejected(data: bytes) -> None:
    with pytest.raises(ValidationError):
        HarnessState.model_validate_json(data)


def test_subclass_retains_its_custom_serializer() -> None:
    class CustomState(HarnessState):
        @model_serializer
        def custom(self) -> dict[str, str]:
            return {"custom": self.thread_id}

    state = CustomState.new(thread_id="thr_custom")
    encoded = state.to_json_bytes()
    assert encoded == state.model_dump_json().encode()
    assert state.to_json_bytes() == encoded
    assert json.loads(encoded) == {"custom": "thr_custom"}
    # A custom serializer that omits history must not acquire an implicit history read.
    unchecked = state.model_copy(update={"message_history_json": b"[1]"})
    assert unchecked.to_json_bytes() == encoded
