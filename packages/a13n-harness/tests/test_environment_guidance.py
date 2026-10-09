"""Exercise model-visible Environment guidance, not just internal exception text."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from a13n_environment.computer import ComputerPoint
from a13n_environment.models import (
    COMPUTER_ACTIONS,
    FILE_READ_ACTIONS,
    EnvironmentAction,
    EnvironmentError,
    EnvironmentPermissionSet,
)
from a13n_environment.operations import EnvironmentOperations
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.content import request_input_content
from a13n_harness.environment import DynamicEnvironmentCapability, DynamicEnvironmentConfiguration
from a13n_harness.environment.advanced import create_empty_environment_runtime, create_environment_runtime
from a13n_harness.environment.providers import EnvironmentRuntimeMount
from a13n_harness.model_context import ModelContextProjectionRequest, ModelContextRequestKind, user_prompt_content
from a13n_harness.toolsets.computer import ComputerToolset
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_computer_environment import Computer
from .test_dynamic_environment import _local_mount
from .test_environment_core import _Binding, _instance

pytestmark = pytest.mark.anyio


def _desktop(name, actions=COMPUTER_ACTIONS):
    computer = Computer()
    binding = _Binding(
        name,
        families=frozenset({"computer"}),
        operations=EnvironmentOperations(computer=computer),
        permissions=COMPUTER_ACTIONS,
    )
    computer.binding = binding
    return (
        EnvironmentRuntimeMount(binding=binding, permission_ceiling=EnvironmentPermissionSet(operations=actions)),
        computer,
    )


def _example_mounts(root: Path):
    primary, primary_computer = _desktop("desktop-primary")
    editor, editor_computer = _desktop("desktop-editor")
    observer, observer_computer = _desktop(
        "desktop-observer", frozenset({EnvironmentAction.COMPUTER_DESCRIBE, EnvironmentAction.COMPUTER_OBSERVE})
    )
    return {
        "workspace": _local_mount(root, mount_path="/project", process_output=True),
        "reference": _local_mount(root, mount_path="/reference", operations=FILE_READ_ACTIONS),
        "desktop-primary": primary,
        "desktop-editor": editor,
        "desktop-observer": observer,
    }, {"desktop-primary": primary_computer, "desktop-editor": editor_computer, "desktop-observer": observer_computer}


async def _capture_example(root: Path, *, instructions=True, computer_enabled=True):
    """Capture actual outgoing guidance and schemas for an offline example or model evaluation."""
    mounts, _ = _example_mounts(root)
    return await _capture_environment(
        create_environment_runtime(mounts=mounts, default_mount="workspace"),
        instructions=instructions,
        configuration=DynamicEnvironmentConfiguration(computer_enabled=computer_enabled),
    )


def _model_view(messages, info):
    return {
        "instructions": info.instructions or "",
        "tools": [
            {"name": tool.name, "description": tool.description, "parameters": tool.parameters_json_schema}
            for tool in info.function_tools
        ],
        "environment": [
            item.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
            for item in user_prompt_content(part)
            if item.content.startswith("Current Environment mounts")
        ][-1],
    }


async def _capture_environment(runtime, *, instructions=True, configuration=None):
    captured = {}

    async def stream(messages, info):
        captured.update(_model_view(messages, info))
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(configuration or DynamicEnvironmentConfiguration()),),
    )
    result = await executable.run(
        "Inspect the available environments.",
        bindings=RunBindings.embedded(
            environment=runtime,
            toolset_instructions=instructions,
        ),
    )
    assert result.output_or_raise() == "done"
    return captured


@pytest.mark.parametrize("instructions", [True, False])
async def test_model_gets_one_routing_guide_exact_actions_and_described_selectors(tmp_path, instructions):
    captured = await _capture_example(tmp_path, instructions=instructions)
    guide = captured["instructions"]
    for name in ("environment-routing", "environment-computer", "environment-shell"):
        assert guide.count(f'<tool-instruction name="{name}">') == int(instructions)
    tools = {tool["name"]: tool for tool in captured["tools"]}
    assert "does not inherit" in tools["computer_type_text"]["parameters"]["properties"]["alias"]["description"]
    assert "this Run" in tools["computer_click"]["parameters"]["properties"]["observation_id"]["description"]
    assert "default target" in tools["computer_observe"]["parameters"]["properties"]["target_id"]["description"]
    assert "UTF-8 bytes" in tools["computer_type_text"]["parameters"]["properties"]["text"]["description"]
    assert "must agree with alias" in tools["shell_exec"]["parameters"]["properties"]["cwd"]["description"]
    payload = json.loads(captured["environment"].split("\n", 1)[1])
    mounts = {mount["name"]: mount for mount in payload["mounts"]}
    assert mounts["desktop-observer"]["operations"] == {"computer": ["describe", "observe"]}
    assert "type_text" in mounts["desktop-editor"]["operations"]["computer"]
    assert "write_text" not in mounts["reference"]["operations"]["files"]
    assert "write_text" in mounts["workspace"]["operations"]["files"]
    assert "computer" not in mounts["workspace"]["operations"]
    for mount in mounts.values():
        assert "read_only" not in mount
        assert "state" not in mount["operations"]
        assert "mount_id" not in mount


async def test_disabled_computer_has_no_tools_or_specific_guidance(tmp_path):
    captured = await _capture_example(tmp_path, computer_enabled=False)
    assert "environment-routing" in captured["instructions"]
    assert "environment-computer" not in captured["instructions"]
    assert not any(tool["name"].startswith("computer_") for tool in captured["tools"])


@pytest.mark.parametrize("default_mount", ["desktop", None])
@pytest.mark.parametrize("instructions", [True, False])
async def test_single_desktop_needs_routing_only_without_a_default(default_mount, instructions):
    mount, _ = _desktop("desktop")
    captured = await _capture_environment(
        create_environment_runtime(mounts={"desktop": mount}, default_mount=default_mount),
        instructions=instructions,
    )
    guide = captured["instructions"]
    assert guide.count('<tool-instruction name="environment-routing">') == int(instructions and default_mount is None)
    assert guide.count('<tool-instruction name="environment-computer">') == int(instructions)
    if instructions:
        assert "foreground focus" in guide
        assert "never blindly repeat input" in guide
        assert "most recent 16" in guide
    payload = json.loads(captured["environment"].split("\n", 1)[1])
    assert payload["default_mount"] == default_mount
    assert payload["mounts"][0]["name"] == "desktop"
    assert "type_text" in payload["mounts"][0]["operations"]["computer"]
    assert len(captured["tools"]) == 8


async def test_no_exposed_environment_tools_need_no_routing_guide(tmp_path):
    mounts, _ = _example_mounts(tmp_path)
    captured = await _capture_environment(
        create_environment_runtime(mounts=mounts, default_mount="workspace"),
        configuration=DynamicEnvironmentConfiguration(files_enabled=False, shell_enabled=False, computer_enabled=False),
    )
    assert captured["tools"] == []
    assert '<tool-instruction name="environment-' not in captured["instructions"]
    assert len(json.loads(captured["environment"].split("\n", 1)[1])["mounts"]) == 5


@pytest.mark.parametrize("instructions", [True, False])
async def test_routing_guide_tracks_empty_single_multiple_and_default_changes(tmp_path, instructions):
    runtime = create_empty_environment_runtime()
    captured = []

    def advance() -> str:
        return "advanced"

    async def stream(messages, info):
        captured.append(_model_view(messages, info))
        step = len(captured)
        if step == 1:
            await runtime.mount("workspace", _local_mount(tmp_path, mount_path="/project"), make_default=True)
        elif step == 2:
            await runtime.mount(
                "reference", _local_mount(tmp_path, mount_path="/reference", operations=FILE_READ_ACTIONS)
            )
        elif step == 3:
            await runtime.unmount("reference")
        elif step == 4:
            await runtime.set_default(None)
        elif step == 5:
            await runtime.set_default("workspace")
        elif step == 6:
            await runtime.unmount("workspace")
        else:
            yield "done"
            return
        yield {0: DeltaToolCall(name="advance", json_args="{}", tool_call_id=f"advance-{step}")}

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),
            Capability(tools=[advance], id="test-advance"),
        ),
    )
    result = await executable.run(
        "Inspect changing environments.",
        bindings=RunBindings.embedded(environment=runtime, toolset_instructions=instructions),
    )
    assert result.output_or_raise() == "done"
    assert [
        item.value
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for item in request_input_content(message)
        if item.metadata.display
    ] == ["Inspect changing environments."]
    assert "The Environment mounts changed" not in str(result.all_messages())
    expected = [
        (0, None, False),
        (1, "workspace", False),
        (2, "workspace", True),
        (1, "workspace", False),
        (1, None, True),
        (1, "workspace", False),
        (0, None, False),
    ]
    assert len(captured) == len(expected)
    for request, (count, default, routing) in zip(captured, expected, strict=True):
        payload = json.loads(request["environment"].split("\n", 1)[1])
        assert len(payload["mounts"]) == count
        assert payload["default_mount"] == default
        assert request["instructions"].count('<tool-instruction name="environment-routing">') == int(
            instructions and routing
        )
        names = {tool["name"] for tool in request["tools"]} - {"advance"}
        assert bool(names) == bool(count)
        if count:
            assert payload["mounts"][0]["root"] == "/project"
            assert "read_text" in payload["mounts"][0]["operations"]["files"]
        else:
            assert '<tool-instruction name="environment-' not in request["instructions"]


async def test_desktop_provenance_and_explicit_keyboard_alias_keep_input_on_intended_mount(tmp_path):
    mounts, computers = _example_mounts(tmp_path)
    runtime = create_environment_runtime(mounts=mounts, default_mount="desktop-primary")
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        tools = ComputerToolset(env)
        assert (await tools.computer_describe())["alias"] == "desktop-primary"
        observed = await tools.computer_observe(alias="desktop-editor")
        assert observed.return_value["alias"] == "desktop-editor"
        reference = observed.return_value["observation_id"]
        assert (await tools.computer_click(reference, ComputerPoint(x=1, y=1)))["ok"]
        assert (await tools.computer_type_text("hello", alias=observed.return_value["alias"]))["ok"]
        assert computers["desktop-primary"].inputs == []
        assert [request.kind for request in computers["desktop-editor"].inputs] == ["click", "type_text"]
        # Observing/clicking never changes the default. The docs must not imply otherwise.
        assert (await tools.computer_type_text("default"))["ok"]
        assert [request.kind for request in computers["desktop-primary"].inputs] == ["type_text"]


async def test_public_errors_explain_selection_denial_and_observation_recovery(tmp_path):
    mounts, computers = _example_mounts(tmp_path)
    runtime = create_environment_runtime(mounts=mounts)
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        tools = ComputerToolset(env)
        missing_default = await tools.computer_observe()
        assert missing_default["error"]["details"]["reason"] == "default_mount_unavailable"
        assert "explicitly" in missing_default["error"]["details"]["hint"]
        unknown = await tools.computer_observe(alias="invented")
        assert unknown["error"]["details"]["reason"] == "mount_selection_unavailable"
        denied = await tools.computer_type_text("denied", alias="desktop-observer")
        assert denied["error"]["code"] == "environment_denied"
        assert "desktop-observer" in denied["error"]["details"]["hint"]
        assert "environment.computer.type_text" in denied["error"]["details"]["hint"]
        assert "mount_id" not in denied["error"]["details"]
        with pytest.raises(EnvironmentError) as conflict:
            env.resolve_path("/reference/file", alias="workspace")
        public = conflict.value.safe_projection()
        assert public["details"]["reason"] == "alias_path_mismatch"
        assert "reference" in public["details"]["hint"]
        missing = await tools.computer_click("obs-missing", ComputerPoint(x=0, y=0))
        assert missing["error"]["details"]["field"] == "observation_id"
        assert "computer_observe" in missing["error"]["details"]["hint"]
        assert all(computer.inputs == [] for computer in computers.values())


async def test_replacement_and_new_toolset_cannot_restore_observations_from_text():
    mount, computer = _desktop("desktop")
    runtime = create_environment_runtime(mounts={"desktop": mount}, default_mount="desktop")
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        await runtime._activate()
        tools = ComputerToolset(env)
        observed = await tools.computer_observe()
        reference = observed.return_value["observation_id"]
        new_tools = ComputerToolset(env)
        absent = await new_tools.computer_click(reference, ComputerPoint(x=0, y=0))
        assert absent["error"]["details"]["reason"] == "observation_unavailable"
        replacement, replacement_computer = _desktop("replacement")
        await runtime.replace("desktop", replacement)
        stale = await tools.computer_click(reference, ComputerPoint(x=0, y=0))
        assert stale["ok"] is False
        assert stale["error"]["details"]["reason"] == "observation_stale"
        assert "reassess" in stale["error"]["details"]["hint"]
        assert computer.inputs == replacement_computer.inputs == []
        projection = await env.project_model_context(ModelContextProjectionRequest(kind=ModelContextRequestKind.INPUT))
        assert '"computer"' in projection.blocks[0].content
