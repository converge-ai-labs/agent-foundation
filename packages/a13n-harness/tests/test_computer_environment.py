from __future__ import annotations

from datetime import UTC, datetime

import pytest
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.environment.providers import EnvironmentRuntimeMount
from a13n_harness.providers.environment.computer import (
    ComputerActionResult,
    ComputerClick,
    ComputerDescription,
    ComputerObservation,
    ComputerPoint,
    ComputerScreenshot,
    ComputerTypeText,
)
from a13n_harness.providers.environment.models import (
    COMPUTER_ACTIONS,
    FILE_EXECUTION_ACTIONS,
    EnvironmentAction,
    EnvironmentError,
    EnvironmentOperationReceipt,
    EnvironmentPermissionSet,
)
from a13n_harness.providers.environment.operations import EnvironmentOperations
from a13n_harness.toolsets.computer import ComputerToolset
from pydantic_ai import BinaryContent, ToolReturn

from .test_environment_core import _Binding, _instance

pytestmark = pytest.mark.anyio


class Computer:
    def __init__(self) -> None:
        self.binding = None
        self.inputs = []

    async def describe(self):
        return ComputerDescription(targets=(), observe_ready=True, input_ready=True)

    async def observe(self, *, target_id=None, max_dimension=1280):
        return ComputerScreenshot(
            ComputerObservation(
                mount_id=self.binding.mount_id,
                observed_generation=self.binding.bound.descriptor.generation,
                observation_id="native-observation",
                target_id="display-one",
                width=640,
                height=480,
                mime_type="image/png",
                captured_at=datetime.now(UTC),
            ),
            b"image",
        )

    async def execute(self, request):
        self.inputs.append(request)
        return ComputerActionResult(
            receipt=EnvironmentOperationReceipt(
                mount_id=self.binding.mount_id,
                observed_generation=self.binding.bound.descriptor.generation,
                operation_id="op-one",
                stage="completed",
                outcome="succeeded",
            ),
            effect="executed",
            input_cleanup_complete=True,
        )


def mount(actions):
    computer = Computer()
    binding = _Binding(
        "desktop",
        families=frozenset({"computer"}),
        operations=EnvironmentOperations(computer=computer),
        permissions=COMPUTER_ACTIONS,
    )
    computer.binding = binding
    runtime = create_environment_runtime(
        mounts={
            "desktop": EnvironmentRuntimeMount(
                binding=binding, permission_ceiling=EnvironmentPermissionSet(operations=actions)
            )
        },
        default_mount="desktop",
    )
    return runtime, computer


async def test_legacy_permission_default_never_exposes_desktop_and_denies_dispatch():
    runtime, computer = mount(FILE_EXECUTION_ACTIONS)
    async with runtime.bind(
        thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}
    ) as environment:
        assert not ComputerToolset(environment).get_toolset().tools
        with pytest.raises(EnvironmentError) as denied:
            await environment.computer.execute(ComputerTypeText(text="hello"))
        assert denied.value.code == "environment_denied"
        assert computer.inputs == []


async def test_observe_only_mount_returns_direct_image_and_does_not_allow_input():
    runtime, computer = mount(frozenset({EnvironmentAction.COMPUTER_OBSERVE}))
    async with runtime.bind(
        thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}
    ) as environment:
        tools = ComputerToolset(environment)
        assert set(tools.get_toolset().tools) == {"computer_observe"}
        result = await tools.computer_observe(alias="desktop")
        assert isinstance(result, ToolReturn)
        assert isinstance(result.content[0], BinaryContent)
        assert result.content[0].vendor_metadata == {"display": False}
        assert "mount_id" not in result.return_value
        denied = await tools.computer_click(result.return_value["observation_id"], ComputerPoint(x=1, y=1))
        assert denied["ok"] is False and denied["error"]["code"] == "environment_denied"
        assert computer.inputs == []


async def test_pointer_reference_is_bound_to_mount_and_tool_cache_is_bounded():
    runtime, computer = mount(COMPUTER_ACTIONS)
    async with runtime.bind(
        thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}
    ) as environment:
        tools = ComputerToolset(environment)
        result = await tools.computer_observe()
        reference = result.return_value["observation_id"]
        clicked = await tools.computer_click(reference, ComputerPoint(x=10, y=20))
        assert clicked["ok"] and len(computer.inputs) == 1
        assert computer.inputs[0].observation.observation_id == "native-observation"
        screenshot = await environment.computer.observe()
        stale = screenshot.observation.model_copy(update={"observed_generation": "old"})
        with pytest.raises(EnvironmentError) as failure:
            await environment.computer.execute(ComputerClick(observation=stale, point=ComputerPoint(x=0, y=0)))
        assert failure.value.code == "environment_stale_mount"
        for _ in range(16):
            await tools.computer_observe()
        expired = await tools.computer_click(reference, ComputerPoint(x=0, y=0))
        assert expired["ok"] is False and len(computer.inputs) == 1
