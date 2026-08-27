from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

from a13n_agent_app_example import build_agent, run_basic_agent

pytestmark = pytest.mark.anyio


async def test_basic_agent_runs_through_harness_with_fresh_local_bindings() -> None:
    observed_message_counts: list[int] = []

    async def respond(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str]:
        del info
        observed_message_counts.append(len(messages))
        yield "deterministic result"

    run_result = await run_basic_agent(
        "Run the minimal path.",
        model=FunctionModel(stream_function=respond),
    )

    assert run_result.status == "completed"
    assert run_result.output_or_raise() == "deterministic result"
    assert run_result.thread_id.startswith("thread-")
    assert run_result.run_id.startswith("run-")
    assert run_result.state is not None
    assert run_result.state.thread_id == run_result.thread_id
    assert run_result.usage.requests == 1
    assert observed_message_counts == [1]


async def test_agent_definition_ignores_ambient_plugins(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("A13N_HARNESS_PLUGIN_CONFIG_ENABLED", "true")
    monkeypatch.setenv(
        "A13N_HARNESS_PLUGIN_CONFIG_FILE",
        str(tmp_path / "missing-harness-plugins.yaml"),
    )

    async def respond(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = build_agent(
        model=FunctionModel(stream_function=respond),
    )

    assert executable.definition.capabilities == ()
    assert executable.definition.plugins == ()
    assert executable.definition.subagents == ()
    await executable.close()
