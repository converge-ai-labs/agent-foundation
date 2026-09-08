from __future__ import annotations

from copy import copy
from pathlib import Path
from typing import Literal

import pytest
import yaml
from a13n_harness import AgentContext
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.composition.reconstruction import _ToolAllowlistCapability
from a13n_harness_ui.errors import CompositionError
from a13n_harness_ui.interactive.backend import SessionBackend
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset

pytestmark = pytest.mark.anyio


class _CopyingToolset(FunctionToolset[AgentContext]):
    """Exercise each native wrapper copy path independently during Agent execution."""

    def __init__(self, phase: Literal["run", "step"], calls: list[str]) -> None:
        self.phase = phase
        self.calls = calls

        def allowed() -> str:
            calls.append("allowed")
            return "allowed result"

        def hidden() -> str:
            pytest.fail("The allowlist must hide this tool.")

        super().__init__([allowed, hidden])

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractToolset[AgentContext]:
        if self.phase == "run":
            self.calls.append("run copy")
            return copy(self)
        return self

    async def for_run_step(self, ctx: RunContext[AgentContext]) -> AbstractToolset[AgentContext]:
        if self.phase == "step":
            self.calls.append("step copy")
            return copy(self)
        return self


@pytest.mark.parametrize("phase", ["run", "step"])
@pytest.mark.parametrize("names", [frozenset({"allowed"}), frozenset()])
async def test_allowlist_survives_native_agent_lifecycle_copies(
    phase: Literal["run", "step"], names: frozenset[str]
) -> None:
    calls: list[str] = []
    model = TestModel()
    agent = Agent(
        model,
        deps_type=AgentContext,
        toolsets=[_CopyingToolset(phase, calls)],
        capabilities=[_ToolAllowlistCapability(names)],
    )

    # Reusing the Agent also checks that the original allowlist remains intact.
    for _ in range(2):
        result = await agent.run("Use every available tool.")
        assert result.output
        assert model.last_model_request_parameters is not None
        assert {tool.name for tool in model.last_model_request_parameters.function_tools} == names

    assert f"{phase} copy" in calls
    assert calls.count("allowed") == (2 if names else 0)


@pytest.mark.parametrize("phase", ["run", "step"])
async def test_unavailable_allowlist_name_still_fails_after_lifecycle_copy(
    phase: Literal["run", "step"],
) -> None:
    model = TestModel()
    agent = Agent(
        model,
        deps_type=AgentContext,
        toolsets=[_CopyingToolset(phase, [])],
        capabilities=[_ToolAllowlistCapability(frozenset({"missing"}))],
    )

    with pytest.raises(CompositionError) as error:
        await agent.run("Use the selected tool.")

    assert error.value.code == "tool_selection_missing"
    assert error.value.details == {"tool": "missing"}
    assert model.last_model_request_parameters is None


async def test_agent_yaml_shell_allowlist_reaches_a_real_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configuration = tmp_path / "a13n-harness-ui.yaml"
    configuration.write_text('schema_version: "1"\n')
    resources = {
        "models/main.yaml": {
            "kind": "model",
            "id": "model-main",
            "name": "Main",
            "route": "openai:gpt-5",
            "authentication": {"kind": "api_key", "env": "TEST_KEY"},
        },
        "agents/main.yaml": {
            "kind": "agent",
            "id": "agent-main",
            "name": "Main",
            "model": "model-main",
            "capabilities": [
                {"capability": "dynamic_environment", "configuration": {"files_enabled": True, "shell_enabled": True}}
            ],
            "tools": ["shell_exec"],
        },
    }
    for relative, resource in resources.items():
        target = tmp_path / relative
        target.parent.mkdir(exist_ok=True)
        target.write_text(yaml.safe_dump({"schema_version": "1", **resource}))

    surfaces: list[set[str]] = []

    async def build(self, recipe, authentication):
        async def stream(messages, info):
            surfaces.append({tool.name for tool in info.function_tools})
            yield "Allowlist run completed."

        return FunctionModel(stream_function=stream)

    monkeypatch.setattr("pydantic_ai.models.ALLOW_MODEL_REQUESTS", False)
    monkeypatch.setattr(HarnessUiModelResolver, "_api_key_model", build)
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False),
        configuration_path=configuration,
    ) as app:
        backend = SessionBackend(app, CliRequest(agent_id="agent-main"), tmp_path, Status())
        renderer = StreamRenderer(backend.status)
        assert await backend.execute(renderer, prompt="Inspect the available tools.") == ""
        assert "Allowlist run completed." in renderer.drain()

    assert len(surfaces) == 1
    assert "shell_exec" in surfaces[0]
    assert not {"view", "write", "glob", "grep"} & surfaces[0]
