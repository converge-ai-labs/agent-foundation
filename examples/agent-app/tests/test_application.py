from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from a13n_harness import RunError
from pydantic_ai.messages import ModelMessage, ModelRequest
from pydantic_ai.models.function import AgentInfo, FunctionModel

from a13n_agent_app_example import ConversationApplication

pytestmark = pytest.mark.anyio


def _conversation_model(
    name: str,
    observed_history_sizes: list[int],
) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        observed_history_sizes.append(len(messages))
        turn_count = sum(isinstance(message, ModelRequest) for message in messages)
        yield f"{name}:"
        yield f"turn-{turn_count}"

    return FunctionModel(stream_function=stream, model_name=name)


async def _collect_turn(application: ConversationApplication, prompt: str) -> list[str]:
    return [text async for text in application.stream_turn(prompt)]


async def test_conversation_streams_repeated_turns_and_recovers_from_harness_state(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "conversation-state.json"
    initial_history_sizes: list[int] = []
    initial_application = ConversationApplication(
        model=_conversation_model("initial", initial_history_sizes),
        state_path=state_path,
    )

    first_chunks = await _collect_turn(initial_application, "first message")
    first_state = await initial_application.load_state()
    second_chunks = await _collect_turn(initial_application, "second message")
    second_state = await initial_application.load_state()

    assert first_chunks == ["initial:", "turn-1"]
    assert second_chunks == ["initial:", "turn-2"]
    assert first_state is not None
    assert second_state is not None
    assert second_state.thread_id == first_state.thread_id
    assert len(second_state.message_history) > len(first_state.message_history)
    assert initial_history_sizes[1] > initial_history_sizes[0]

    recovered_history_sizes: list[int] = []
    recovered_application = ConversationApplication(
        model=_conversation_model("recovered", recovered_history_sizes),
        state_path=state_path,
    )
    recovered_before_turn = await recovered_application.load_state()
    third_chunks = await _collect_turn(recovered_application, "third message after restart")
    recovered_after_turn = await recovered_application.load_state()

    assert third_chunks == ["recovered:", "turn-3"]
    assert recovered_before_turn == second_state
    assert recovered_after_turn is not None
    assert recovered_after_turn.thread_id == first_state.thread_id
    assert len(recovered_after_turn.message_history) > len(second_state.message_history)
    assert recovered_history_sizes == [initial_history_sizes[1] + 2]
    assert state_path.read_text(encoding="utf-8").startswith("{\n")


async def test_failed_turn_does_not_replace_the_last_completed_state(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "conversation-state.json"
    application = ConversationApplication(
        model=_conversation_model("completed", []),
        state_path=state_path,
    )
    _ = await _collect_turn(application, "completed turn")
    completed_payload = state_path.read_text(encoding="utf-8")

    async def fail_after_text(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "partial text"
        raise RuntimeError("simulated provider interruption")

    failing_application = ConversationApplication(
        model=FunctionModel(stream_function=fail_after_text, model_name="failing"),
        state_path=state_path,
    )

    with pytest.raises(RunError) as exc_info:
        _ = await _collect_turn(failing_application, "failed turn")
    assert exc_info.value.code == "agent_run_failed"

    assert state_path.read_text(encoding="utf-8") == completed_payload
