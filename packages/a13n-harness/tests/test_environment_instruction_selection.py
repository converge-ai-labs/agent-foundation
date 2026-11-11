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


async def capture(actions, *, selected=None, instructions=True, computer=True, other_actions=None):
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
        "inspect", bindings=RunBindings.embedded(environment=runtime, toolset_instructions=instructions)
    )
    assert result.output_or_raise() == "done"
    assert all(source.entered == 0 for source in sources.values())
    assert len(calls) == 1
    return {tool.name for tool in calls[0].function_tools}, calls[0].instructions or ""


@pytest.mark.parametrize(
    "actions,selected,present,absent",
    [
        (frozenset(), None, (), ("environment-computer",)),
        ({Action.COMPUTER_DESCRIBE}, None, ("computer_describe",), ("obs-", "Clipboard", "Native accessibility")),
        (
            {Action.COMPUTER_OBSERVE},
            None,
            ("obs-", "coordinate_space"),
            ("computer_zoom", "Clipboard", "Native accessibility", "input_cleanup_complete"),
        ),
        (
            {Action.COMPUTER_ZOOM},
            None,
            ("computer_zoom", "source_observation_id"),
            ("Clipboard", "Native accessibility"),
        ),
        (
            {Action.COMPUTER_READ_CLIPBOARD},
            None,
            ("Clipboard", "cleanup_complete"),
            ("obs-", "coordinate_space", "Native accessibility", "input_cleanup_complete"),
        ),
        (
            ACCESSIBILITY_READ_ACTIONS,
            None,
            ("Native accessibility", "range-"),
            (
                "Computer coordinate_space",
                "input_cleanup_complete",
                "application_outcome",
                "selection_select",
                "selection_add_child",
            ),
        ),
        (
            {Action.COMPUTER_UIA_INVOKE},
            None,
            ("application_outcome",),
            ("Computer coordinate_space", "selection_select", "selection_add_child", "Clipboard"),
        ),
        (
            {Action.COMPUTER_ATSPI_DO_ACTION},
            None,
            ("application_outcome",),
            ("selection_select", "selection_add_child", "Computer coordinate_space"),
        ),
        (
            {Action.COMPUTER_UIA_SELECTION_SELECT},
            None,
            ("selection_select",),
            ("selection_add_child",),
        ),
        (
            {Action.COMPUTER_ATSPI_SELECTION_ADD_CHILD},
            None,
            ("selection_add_child",),
            ("selection_select",),
        ),
        (
            {Action.COMPUTER_AX_PERFORM_ACTION},
            None,
            ("application_outcome",),
            ("selection_select", "selection_add_child", "Computer coordinate_space"),
        ),
        (
            COMPUTER_ACTIONS,
            frozenset({"computer_observe"}),
            ("obs-",),
            ("Native accessibility", "computer_zoom", "Clipboard"),
        ),
        (COMPUTER_ACTIONS, frozenset(), (), ("environment-computer", "Computer coordinate_space")),
    ],
)
async def test_computer_instruction_sections_follow_actions_and_exact_tool_selection(
    actions, selected, present, absent
):
    names, instructions = await capture(frozenset(actions), selected=selected)
    if selected is not None:
        assert names == selected
    for text in present:
        assert text in instructions
    for text in absent:
        assert text not in instructions


async def test_mixed_mounts_contribute_only_their_available_workflows_without_activation():
    names, instructions = await capture(frozenset({Action.COMPUTER_OBSERVE}), other_actions=ACCESSIBILITY_READ_ACTIONS)
    assert "computer_observe" in names
    assert "computer_accessibility_roots" in names
    assert "Native accessibility" in instructions
    assert "Computer coordinate_space" in instructions
    assert "Clipboard" not in instructions
    assert "application_outcome" not in instructions
    assert "selection_select" not in instructions


async def test_environment_instruction_switch_preserves_tools_but_removes_guidance():
    names, instructions = await capture(COMPUTER_ACTIONS, instructions=False)
    assert "computer_accessibility_roots" in names
    assert "computer_observe" in names
    assert "environment-computer" not in instructions
    assert "Computer coordinate_space" not in instructions


@pytest.mark.parametrize(
    "actions,selected,present,absent",
    [
        (
            {Action.SHELL_EXEC},
            None,
            ("Agent-wide tool timeout",),
            ("Use `shell_wait`", "Use `shell_info", "Use `shell_input`", "Use `shell_signal`"),
        ),
        (
            {Action.PROCESS_INSPECT},
            None,
            ("Use `shell_info", "process-*"),
            ("Use `shell_exec`", "Use `shell_wait`", "Use `shell_input`", "Use `shell_signal`"),
        ),
        (
            frozenset(Action),
            frozenset({"shell_exec"}),
            ("Use `shell_exec`",),
            ("Use `shell_wait`", "Use `shell_info", "Use `shell_input`", "Use `shell_signal`"),
        ),
        (
            frozenset(Action),
            frozenset({"shell_wait"}),
            ("Use `shell_wait`",),
            ("Use `shell_exec`", "Use `shell_info", "Use `shell_input`", "Use `shell_signal`"),
        ),
    ],
)
async def test_shell_instruction_sections_follow_exposed_controls(actions, selected, present, absent):
    _, instructions = await capture(frozenset(actions), selected=selected, computer=False)
    for text in present:
        assert text in instructions
    for text in absent:
        assert text not in instructions


async def test_lazy_support_narrowing_refreshes_context_tools_schemas_and_instructions():
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
        ctx = SimpleNamespace(deps=SimpleNamespace(toolset_instructions=True))
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
        assert "Clipboard" not in after_instructions
        assert "selection_select" not in after_instructions
        assert "selection_add_child" not in after_instructions
        assert "Computer coordinate_space" not in after_instructions
        schema = after.tools["computer_uia"].function_schema.json_schema
        assert schema["$defs"]["UIACommand"]["properties"]["op"]["const"] == "invoke"
        projection = await env.project_model_context(request)
        payload = json.loads(projection.blocks[0].content.split("\n", 1)[1])
        assert payload["mounts"][0]["operations_source"] == "execution"
        assert "observe" not in payload["mounts"][0]["operations"]["computer"]
        assert source.entered == 1
