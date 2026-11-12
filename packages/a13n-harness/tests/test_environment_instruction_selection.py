"""Operational guidance follows the effective tools without activating lazy mounts."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.environment import EnvironmentReadinessRequirement
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.environment.configuration import DynamicEnvironmentConfiguration
from a13n_harness.environment.dynamic import DynamicEnvironmentCapability
from a13n_harness.environment.models import (
    ACCESSIBILITY_READ_ACTIONS,
    COMPUTER_ACTIONS,
    ENVIRONMENT_ACTION_DISPATCH,
    EnvironmentPermissionSet,
)
from a13n_harness.environment.models import (
    EnvironmentAction as Action,
)
from a13n_harness.environment.operations import EnvironmentOperations
from a13n_harness.environment.sources import EnvironmentMount
from a13n_harness.model_context import ModelContextProjectionRequest, ModelContextRequestKind
from a13n_harness.toolsets.computer import ComputerToolset
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

from .test_accessibility_environment import _Accessibility
from .test_environment_core import _Binding, _instance

pytestmark = pytest.mark.anyio


async def capture(actions, *, selected=None, instructions=True, computer=True, other_actions=None, reference_root=None):
    calls: list[AgentInfo] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        calls.append(info)
        yield "done"

    sources = {
        name: _Binding(
            name,
            families=frozenset(ENVIRONMENT_ACTION_DISPATCH[action].family for action in allowed),
            permissions=frozenset(allowed),
            operations=EnvironmentOperations(),
        )
        for name, allowed in (("one", actions), ("two", other_actions))
        if allowed is not None
    }
    runtime = create_environment_runtime(
        mounts={
            name: EnvironmentMount(source, permission_ceiling=source.descriptor.permissions)
            for name, source in sources.items()
        },
        default_mount="one",
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            DynamicEnvironmentCapability(
                DynamicEnvironmentConfiguration(
                    files_enabled=False,
                    shell_enabled=not computer,
                    computer_enabled=computer,
                    computer_tools=selected if computer else None,
                    shell_tools=selected if not computer else None,
                )
            ),
        ),
    )
    result = await executable.run(
        "inspect",
        bindings=RunBindings.embedded(
            environment=runtime, toolset_instructions=instructions, instruction_reference_root=reference_root
        ),
    )
    assert result.output_or_raise() == "done"
    assert all(source.entered == 0 for source in sources.values())
    assert len(calls) == 1
    return {tool.name for tool in calls[0].function_tools}, calls[0].instructions or ""


@pytest.mark.parametrize(
    "actions,selected",
    [
        (frozenset(), None),
        ({Action.COMPUTER_DESCRIBE}, None),
        ({Action.COMPUTER_OBSERVE}, None),
        ({Action.COMPUTER_ZOOM}, None),
        ({Action.COMPUTER_READ_CLIPBOARD}, None),
        (ACCESSIBILITY_READ_ACTIONS, None),
        ({Action.COMPUTER_UIA_INVOKE}, None),
        ({Action.COMPUTER_ATSPI_DO_ACTION}, None),
        ({Action.COMPUTER_UIA_SELECTION_SELECT}, None),
        ({Action.COMPUTER_ATSPI_SELECTION_ADD_CHILD}, None),
        ({Action.COMPUTER_AX_PERFORM_ACTION}, None),
        (COMPUTER_ACTIONS, frozenset({"computer_observe"})),
        (COMPUTER_ACTIONS, frozenset()),
    ],
)
async def test_computer_guidance_is_grouped_while_tool_selection_and_coordinates_remain_exact(actions, selected):
    names, instructions = await capture(frozenset(actions), selected=selected)
    if selected is not None:
        assert names == selected
    assert ("<tool-name>environment-computer</tool-name>" in instructions) is bool(names)
    for text in ("Clipboard", "Native accessibility", "selection_select", "selection_add_child"):
        assert (text in instructions) is bool(names)
    pointer_tools = {
        "computer_observe",
        "computer_zoom",
        "computer_click",
        "computer_move",
        "computer_drag",
        "computer_scroll",
    }
    assert ("Computer coordinate_space" in instructions) is bool(names & pointer_tools)


async def test_mixed_mounts_contribute_one_group_without_activation():
    names, instructions = await capture(frozenset({Action.COMPUTER_OBSERVE}), other_actions=ACCESSIBILITY_READ_ACTIONS)
    assert "computer_observe" in names
    assert "computer_accessibility_roots" in names
    assert "Native accessibility" in instructions
    assert "Computer coordinate_space" in instructions
    assert instructions.count("<tool-name>environment-computer</tool-name>") == 1
    assert "Clipboard" in instructions
    assert "application_outcome" in instructions
    assert "selection_select" in instructions


async def test_environment_instruction_switch_preserves_tools_but_removes_guidance():
    names, instructions = await capture(COMPUTER_ACTIONS, instructions=False)
    assert "computer_accessibility_roots" in names
    assert "computer_observe" in names
    assert "environment-computer" not in instructions
    assert "Computer coordinate_space" not in instructions


@pytest.mark.parametrize(
    "actions,selected",
    [
        ({Action.SHELL_EXEC}, None),
        ({Action.PROCESS_INSPECT}, None),
        (frozenset(Action), frozenset({"shell_exec"})),
        (frozenset(Action), frozenset({"shell_wait"})),
    ],
)
async def test_shell_guidance_is_grouped_while_exposed_controls_remain_selected(actions, selected):
    names, instructions = await capture(frozenset(actions), selected=selected, computer=False)
    if selected is not None:
        assert names == selected
    assert instructions.count("<tool-name>environment-shell</tool-name>") == 1
    for text in ("Use `shell_exec`", "Use `shell_wait`", "Use `shell_info", "Use `shell_input`", "Use `shell_signal`"):
        assert text in instructions


@pytest.mark.parametrize("reference_root", [None, "/guidance/release"])
async def test_lazy_support_narrows_context_tools_and_schemas_while_retaining_group_guidance(reference_root):
    class Source(_Binding):
        @property
        def descriptor(self):
            return self.bound.descriptor.model_copy(
                update={"permissions": EnvironmentPermissionSet(operations=COMPUTER_ACTIONS)}
            )

    source = Source(
        "one",
        families=frozenset({"computer"}),
        permissions=ACCESSIBILITY_READ_ACTIONS | {Action.COMPUTER_UIA_INVOKE},
        # These facets are not called: the test exercises metadata publication.
        operations=EnvironmentOperations(accessibility=_Accessibility()),
    )
    runtime = create_environment_runtime(
        mounts={
            "one": EnvironmentMount(source, permission_ceiling=EnvironmentPermissionSet(operations=COMPUTER_ACTIONS))
        },
        default_mount="one",
    )
    request = ModelContextProjectionRequest(kind=ModelContextRequestKind.INPUT)
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        computer = ComputerToolset(env)
        before = computer.get_toolset()
        assert "computer_ax" in before.tools
        ctx = SimpleNamespace(
            deps=SimpleNamespace(toolset_instructions=True, instruction_reference_root=reference_root)
        )
        before_instructions = await before.get_instructions(ctx)
        assert "Clipboard" in str(before_instructions)
        assert "selection_select" in str(before_instructions)
        projection = await env.project_model_context(request)
        assert '"operations_source":"configured"' in projection.blocks[0].content
        assert source.entered == 0
        await env.ensure_ready(EnvironmentReadinessRequirement(operations=frozenset({"computer"})))
        after = computer.get_toolset()
        assert "computer_ax" not in after.tools
        assert "computer_atspi" not in after.tools
        assert "computer_observe" not in after.tools
        after_instructions = str(await after.get_instructions(ctx))
        assert "Native accessibility" in after_instructions
        assert "application_outcome" in after_instructions
        assert "Clipboard" in after_instructions
        assert "selection_select" in after_instructions
        assert "selection_add_child" in after_instructions
        assert "Computer coordinate_space" not in after_instructions
        schema = after.tools["computer_uia"].function_schema.json_schema
        assert schema["$defs"]["UIACommand"]["properties"]["op"]["const"] == "invoke"
        projection = await env.project_model_context(request)
        payload = json.loads(projection.blocks[0].content.split("\n", 1)[1])
        assert payload["mounts"][0]["operations_source"] == "execution"
        assert "observe" not in payload["mounts"][0]["operations"]["computer"]
        assert source.entered == 1


@pytest.mark.parametrize("reference_root", [None, "/release-guidance"])
async def test_shell_and_routing_guidance_use_run_reference_root_without_activation(reference_root):
    names, instructions = await capture(
        {Action.SHELL_EXEC},
        computer=False,
        other_actions={Action.SHELL_EXEC},
        reference_root=reference_root,
    )
    assert "shell_exec" in names
    assert "Do not reissue it." in instructions
    assert "Choose the target before acting." in instructions
    assert "Reads are non-consuming" in instructions
    assert ("Live Shell controls end at execution settlement" in instructions) is (reference_root is None)
    assert ("Absolute file paths and shell" in instructions) is (reference_root is None)
    for page in ("environment-shell", "environment-routing"):
        assert (f"/release-guidance/harness/{page}.md" in instructions) is (reference_root is not None)
