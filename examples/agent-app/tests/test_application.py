from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from a13n_environment_provider import (
    DirectLocalEnvironmentProvider,
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
    EnvironmentOperationContext,
)
from a13n_harness import RunError
from pydantic_ai.messages import ModelMessage, ModelRequest
from pydantic_ai.models.function import AgentInfo, FunctionModel

from a13n_agent_app_example import ConversationApplication

pytestmark = pytest.mark.anyio


class _MockEnvironmentProvider(DirectLocalEnvironmentProvider):
    """Offline Environment with observable lifecycle events for this example."""

    def __init__(self, root: Path) -> None:
        super().__init__(
            DirectLocalProviderConfiguration(
                environment_id="agent-app-test",
                root=DirectLocalRootConfiguration(path=root),
            )
        )
        self.lifecycle: list[str] = []

    async def create(self, *, operation: EnvironmentOperationContext):
        self.lifecycle.append("create")
        return await super().create(operation=operation)

    async def destroy(self, state, *, operation: EnvironmentOperationContext) -> None:
        self.lifecycle.append("destroy")
        await super().destroy(state, operation=operation)


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
    async with application.stream_turn(prompt) as stream:
        return [text async for text in stream]


async def test_conversation_streams_multiple_turns_and_recovers_after_restart(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "conversation-state.json"
    initial_environment = _MockEnvironmentProvider(tmp_path)
    initial_history_sizes: list[int] = []

    initial_application = ConversationApplication(
        model=_conversation_model("initial", initial_history_sizes),
        state_path=state_path,
        environment=initial_environment,
    )
    async with initial_application:
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

    recovered_environment = _MockEnvironmentProvider(tmp_path)
    recovered_history_sizes: list[int] = []
    recovered_application = ConversationApplication(
        model=_conversation_model("recovered", recovered_history_sizes),
        state_path=state_path,
        environment=recovered_environment,
    )
    async with recovered_application:
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
    assert initial_environment.lifecycle == ["create", "destroy", "create", "destroy"]
    assert recovered_environment.lifecycle == ["create", "destroy"]


async def test_abandoned_turn_scope_cleans_environment_and_allows_the_next_turn(
    tmp_path: Path,
) -> None:
    environment = _MockEnvironmentProvider(tmp_path)
    application = ConversationApplication(
        model=_conversation_model("scoped", []),
        state_path=tmp_path / "conversation-state.json",
        environment=environment,
    )

    async with application:
        async with application.stream_turn("abandoned turn") as stream:
            assert await anext(stream) == "scoped:"
            assert environment.lifecycle == ["create"]

        assert environment.lifecycle == ["create", "destroy"]
        assert await _collect_turn(application, "completed turn") == ["scoped:", "turn-1"]

    assert environment.lifecycle == ["create", "destroy", "create", "destroy"]


async def test_failed_turn_keeps_the_last_completed_state_and_cleans_environment(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "conversation-state.json"
    environment = _MockEnvironmentProvider(tmp_path)
    completed_application = ConversationApplication(
        model=_conversation_model("completed", []),
        state_path=state_path,
        environment=environment,
    )
    async with completed_application:
        await _collect_turn(completed_application, "completed turn")
    completed_payload = state_path.read_text(encoding="utf-8")

    async def fail_after_text(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "partial text"
        raise RuntimeError("simulated provider interruption")

    failing_application = ConversationApplication(
        model=FunctionModel(stream_function=fail_after_text, model_name="failing"),
        state_path=state_path,
        environment=environment,
    )
    with pytest.raises(RunError) as exc_info:
        async with failing_application:
            await _collect_turn(failing_application, "failed turn")

    assert exc_info.value.code == "agent_run_failed"
    assert state_path.read_text(encoding="utf-8") == completed_payload
    assert environment.lifecycle == ["create", "destroy", "create", "destroy"]
