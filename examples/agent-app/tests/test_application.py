from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest
from a13n_environment_provider import (
    DirectLocalEnvironment,
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_harness import RunError
from pydantic_ai.messages import ModelMessage, ModelRequest
from pydantic_ai.models.function import AgentInfo, FunctionModel

from a13n_agent_app_example import ConversationApplication

pytestmark = pytest.mark.anyio


class _MockEnvironment(DirectLocalEnvironment):
    def __init__(self, configuration: DirectLocalProviderConfiguration, lifecycle: list[str]) -> None:
        super().__init__(configuration)
        self._lifecycle_events = lifecycle

    async def _enter(
        self,
        *,
        thread_id: str,
        run_id: str,
        agent_instance_id: str,
        mount_id: str,
        host_refs: Mapping[str, str],
    ) -> None:
        self._lifecycle_events.append("enter")
        await super()._enter(
            thread_id=thread_id,
            run_id=run_id,
            agent_instance_id=agent_instance_id,
            mount_id=mount_id,
            host_refs=host_refs,
        )

    async def _close(self) -> None:
        try:
            await super()._close()
        finally:
            self._lifecycle_events.append("close")


class _MockEnvironmentFactory:
    """Construct fresh Direct Local adapters with observable Run-local lifecycles."""

    def __init__(self, root: Path) -> None:
        self._configuration = DirectLocalProviderConfiguration(
            environment_id="agent-app-test",
            root=DirectLocalRootConfiguration(path=root),
        )
        self.lifecycle: list[str] = []

    def __call__(self) -> DirectLocalEnvironment:
        self.lifecycle.append("construct")
        return _MockEnvironment(self._configuration, self.lifecycle)


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
    initial_environment = _MockEnvironmentFactory(tmp_path)
    initial_history_sizes: list[int] = []

    initial_application = ConversationApplication(
        model=_conversation_model("initial", initial_history_sizes),
        state_path=state_path,
        environment_factory=initial_environment,
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

    recovered_environment = _MockEnvironmentFactory(tmp_path)
    recovered_history_sizes: list[int] = []
    recovered_application = ConversationApplication(
        model=_conversation_model("recovered", recovered_history_sizes),
        state_path=state_path,
        environment_factory=recovered_environment,
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
    assert initial_environment.lifecycle == ["construct", "enter", "close", "construct", "enter", "close"]
    assert recovered_environment.lifecycle == ["construct", "enter", "close"]


async def test_abandoned_turn_scope_cleans_environment_and_allows_the_next_turn(
    tmp_path: Path,
) -> None:
    environment = _MockEnvironmentFactory(tmp_path)
    application = ConversationApplication(
        model=_conversation_model("scoped", []),
        state_path=tmp_path / "conversation-state.json",
        environment_factory=environment,
    )

    async with application.stream_turn("abandoned turn") as stream:
        assert await anext(stream) == "scoped:"
        assert environment.lifecycle == ["construct", "enter"]

    assert environment.lifecycle == ["construct", "enter", "close"]
    assert await _collect_turn(application, "completed turn") == ["scoped:", "turn-1"]
    assert environment.lifecycle == ["construct", "enter", "close", "construct", "enter", "close"]


async def test_failed_turn_keeps_the_last_completed_state_and_cleans_environment(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "conversation-state.json"
    environment = _MockEnvironmentFactory(tmp_path)
    completed_application = ConversationApplication(
        model=_conversation_model("completed", []),
        state_path=state_path,
        environment_factory=environment,
    )
    await _collect_turn(completed_application, "completed turn")
    completed_payload = state_path.read_text(encoding="utf-8")

    async def fail_after_text(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "partial text"
        raise RuntimeError("simulated provider interruption")

    failing_application = ConversationApplication(
        model=FunctionModel(stream_function=fail_after_text, model_name="failing"),
        state_path=state_path,
        environment_factory=environment,
    )
    with pytest.raises(RunError) as exc_info:
        await _collect_turn(failing_application, "failed turn")

    assert exc_info.value.code == "agent_run_failed"
    assert state_path.read_text(encoding="utf-8") == completed_payload
    assert environment.lifecycle == ["construct", "enter", "close", "construct", "enter", "close"]
