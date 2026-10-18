from __future__ import annotations

from datetime import UTC, datetime

import pytest
from a13n_harness.environment import EnvironmentMount
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.environment.computer import (
    ComputerActionResult,
    ComputerClick,
    ComputerDescription,
    ComputerObservation,
    ComputerPoint,
    ComputerRegion,
    ComputerScreenshot,
    ComputerTypeText,
    ComputerZoom,
    ComputerZoomImage,
)
from a13n_harness.environment.models import (
    COMPUTER_ACTIONS,
    FILE_EXECUTION_ACTIONS,
    EnvironmentAction,
    EnvironmentError,
    EnvironmentOperationReceipt,
    EnvironmentPermissionSet,
)
from a13n_harness.environment.operations import EnvironmentOperations
from a13n_harness.toolsets.computer import ComputerToolset
from pydantic_ai import BinaryContent, ToolReturn

from .test_environment_core import _Binding, _instance

pytestmark = pytest.mark.anyio


class Computer:
    def __init__(self) -> None:
        self.binding = None
        self.inputs = []
        self.zooms = []

    async def describe(self):
        return ComputerDescription(targets=(), observe_ready=True, input_ready=True)

    async def observe(self, *, target_id=None, max_dimension=1280):
        return ComputerScreenshot(
            ComputerObservation(
                execution_id=self.binding.bound.execution_id,
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

    async def zoom(self, observation, region, *, max_dimension=1280):
        self.zooms.append((observation, region, max_dimension))
        return ComputerZoom(
            ComputerZoomImage(
                source_observation_id=observation.observation_id,
                target_id=observation.target_id,
                source_width=observation.width,
                source_height=observation.height,
                region=region,
                image_width=900,
                image_height=600,
                mime_type="image/png",
                captured_at=datetime.now(UTC),
            ),
            b"detail-image",
        )

    async def execute(self, request):
        self.inputs.append(request)
        return ComputerActionResult(
            receipt=EnvironmentOperationReceipt(
                execution_id=self.binding.bound.execution_id,
                observed_generation=self.binding.bound.descriptor.generation,
                operation_id="op-one",
                stage="completed",
                outcome="succeeded",
            ),
            effect="executed",
            input_cleanup_complete=True,
        )


def mount(actions, *, guard=None):
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
            "desktop": EnvironmentMount(
                source=binding, permission_ceiling=EnvironmentPermissionSet(operations=actions), operation_guard=guard
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
        assert result.content[0].vendor_metadata is None
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


async def test_step_scroll_is_explicit_and_bounded_before_dispatch():
    runtime, computer = mount(COMPUTER_ACTIONS)
    async with runtime.bind(
        thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}
    ) as environment:
        tools = ComputerToolset(environment)
        image = await tools.computer_observe()
        reference = image.return_value["observation_id"]
        rejected = await tools.computer_scroll(reference, ComputerPoint(x=0, y=0), delta_y=101, unit="steps")
        assert not rejected["ok"] and not computer.inputs
        accepted = await tools.computer_scroll(reference, ComputerPoint(x=0, y=0), delta_y=100, unit="steps")
        assert accepted["ok"] and computer.inputs[-1].unit == "steps"
        accepted = await tools.computer_scroll(reference, ComputerPoint(x=0, y=0), delta_y=10000)
        assert accepted["ok"] and computer.inputs[-1].unit == "pixels"


async def test_eip_scroll_preserves_legacy_pixels_and_requires_step_advertisement(monkeypatch):
    from a13n_envd_client.eip import v1 as eip
    from a13n_harness.environment.computer import ComputerScroll
    from a13n_harness.providers.environment.eip import computer as adapter

    units = ()
    sent = []

    class Client:
        async def computer_describe(self, request):
            return eip.ComputerDescribeResult(targets=(), observe_ready=True, input_ready=True, scroll_units=units)

        async def computer_scroll(self, request):
            sent.append(request.model_dump(mode="json", exclude_none=True))
            raise RuntimeError("sent")

    monkeypatch.setattr(adapter, "session_client", lambda _: Client())
    provider = adapter.EIPComputerOperations(None, execution_id="desktop", generation="one")
    observation = ComputerObservation(
        execution_id="desktop",
        observed_generation="one",
        observation_id="obs-one",
        target_id="display-one",
        width=100,
        height=100,
        mime_type="image/jpeg",
        captured_at=datetime.now(UTC),
    )
    assert (await provider.describe()).scroll_units == ("pixels",)
    with pytest.raises(EnvironmentError, match="does not support"):
        await provider.execute(ComputerScroll(observation=observation, point=ComputerPoint(x=0, y=0), unit="steps"))
    assert sent == []
    with pytest.raises(EnvironmentError, match="EIP provider operation failed"):
        await provider.execute(ComputerScroll(observation=observation, point=ComputerPoint(x=0, y=0)))
    assert "unit" not in sent[-1]
    units = (eip.ComputerScrollUnit.STEPS,)
    with pytest.raises(EnvironmentError, match="EIP provider operation failed"):
        await provider.execute(ComputerScroll(observation=observation, point=ComputerPoint(x=0, y=0), unit="steps"))
    assert sent[-1]["unit"] == "steps"


@pytest.mark.parametrize("alias", [None, "linux-desktop"])
@pytest.mark.parametrize("provider_supports_text", [False, True])
async def test_text_input_denial_explains_selected_alias_without_switching_desktops(alias, provider_supports_text):
    text_action = EnvironmentAction.COMPUTER_TYPE_TEXT
    no_text = COMPUTER_ACTIONS - {text_action}
    computers = {}
    mounts = {}
    for name, permissions, ceiling in (
        (
            "linux-desktop",
            COMPUTER_ACTIONS if provider_supports_text else no_text,
            no_text if provider_supports_text else COMPUTER_ACTIONS,
        ),
        ("mac-desktop", COMPUTER_ACTIONS, COMPUTER_ACTIONS),
    ):
        computer = Computer()
        binding = _Binding(
            name,
            families=frozenset({"computer"}),
            operations=EnvironmentOperations(computer=computer),
            permissions=permissions,
        )
        computer.binding = binding
        computers[name] = computer
        mounts[name] = EnvironmentMount(source=binding, permission_ceiling=EnvironmentPermissionSet(operations=ceiling))
    runtime = create_environment_runtime(mounts=mounts, default_mount="linux-desktop")
    async with runtime.bind(
        thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}
    ) as environment:
        tools = ComputerToolset(environment)
        assert "computer_type_text" in tools.get_toolset().tools
        assert (await tools.computer_type_text("allowed", alias="mac-desktop"))["ok"]

        # An omitted alias uses the default, not the last successful desktop.
        rejected = await tools.computer_type_text("private text must not appear in errors", alias=alias)
        assert rejected["ok"] is False
        error = rejected["error"]
        assert error["code"] == "environment_denied"
        assert error["details"]["reason"] == "mount_action_denied"
        assert error["details"]["field"] == "alias"
        assert error["details"]["dispatch_stage"] == "pre_dispatch"
        hint = error["details"]["hint"]
        assert "Mount 'linux-desktop'" in hint
        assert "No text input was dispatched" in hint
        assert "Do not retry unchanged or automatically switch desktops" in hint
        assert "physical key chords, not literal text" in hint
        assert "do not change tools or mounts to bypass a denial" in hint
        assert "private text" not in str(rejected)
        assert not computers["linux-desktop"].inputs
        assert [request.text for request in computers["mac-desktop"].inputs] == ["allowed"]


async def test_text_input_provider_failure_does_not_claim_pre_dispatch_or_replace_evidence(monkeypatch):
    runtime, computer = mount(COMPUTER_ACTIONS)

    async def fail(request):
        computer.inputs.append(request)
        raise EnvironmentError(
            "private provider failure",
            code="environment_provider_failure",
            retry_hint="reconcile_first",
            details={"dispatch_stage": "unknown", "hint": "Reconcile possible effects before retrying."},
        )

    monkeypatch.setattr(computer, "execute", fail)
    async with runtime.bind(
        thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}
    ) as environment:
        result = await ComputerToolset(environment).computer_type_text("private text")
        assert result["error"]["code"] == "environment_provider_failure"
        assert result["error"]["details"]["dispatch_stage"] == "unknown"
        assert result["error"]["details"]["hint"] == "Reconcile possible effects before retrying."
        assert result["error"]["retry_hint"] == "reconcile_first"
        assert "private" not in str(result)
        assert len(computer.inputs) == 1


@pytest.mark.parametrize("effect,cleanup", [("partial", True), ("unknown", True), ("executed", False)])
async def test_incomplete_input_explains_reconciliation_and_never_replays(monkeypatch, effect, cleanup):
    runtime, computer = mount(COMPUTER_ACTIONS)
    original = computer.execute

    async def incomplete(request):
        result = await original(request)
        return ComputerActionResult(receipt=result.receipt, effect=effect, input_cleanup_complete=cleanup)

    monkeypatch.setattr(computer, "execute", incomplete)
    async with runtime.bind(
        thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}
    ) as environment:
        result = await ComputerToolset(environment).computer_type_text("private text")
        assert not result["ok"]
        assert result["effect"] == effect
        assert result["input_cleanup_complete"] == cleanup
        assert "do not automatically replay" in result["hint"]
        assert ("may remain held" in result["hint"]) == (not cleanup)
        assert len(computer.inputs) == 1
        assert "private text" not in str(result)


async def test_eip_computer_transport_loss_requires_inspection_not_new_run_replay(monkeypatch):
    from a13n_envd_client import EIPTransportClosedError
    from a13n_harness.providers.environment.eip import computer as adapter

    sent = []

    class Client:
        async def computer_type_text(self, request):
            sent.append(request)
            raise EIPTransportClosedError("private connection detail")

    monkeypatch.setattr(adapter, "session_client", lambda _: Client())
    provider = adapter.EIPComputerOperations(None, execution_id="desktop", generation="one")
    with pytest.raises(EnvironmentError) as caught:
        await provider.execute(ComputerTypeText(text="private text"))
    projection = caught.value.safe_projection()
    assert projection["retry_hint"] == "reconcile_first"
    assert projection["details"]["dispatch_stage"] == "unknown"
    assert "Do not automatically replay" in projection["details"]["hint"]
    assert "private" not in str(projection)
    assert len(sent) == 1


async def test_host_admission_spans_dispatch_and_cannot_broaden_permissions():
    from contextlib import asynccontextmanager

    events = []
    deny = False

    @asynccontextmanager
    async def guard(action):
        events.append(("enter", action))
        if deny:
            raise EnvironmentError("Reserved by Host", code="environment_denied")
        try:
            yield
        finally:
            events.append(("exit", action))

    runtime, computer = mount(COMPUTER_ACTIONS, guard=guard)
    async with runtime.bind(
        thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}
    ) as environment:
        await environment.computer.describe()
        assert events == [("enter", EnvironmentAction.COMPUTER_DESCRIBE), ("exit", EnvironmentAction.COMPUTER_DESCRIBE)]
        deny = True
        with pytest.raises(EnvironmentError, match="Reserved by Host"):
            await environment.computer.execute(ComputerTypeText(text="blocked"))
        assert not computer.inputs
    events.clear()
    runtime, _ = mount(frozenset({EnvironmentAction.COMPUTER_OBSERVE}), guard=guard)
    async with runtime.bind(
        thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}
    ) as environment:
        with pytest.raises(EnvironmentError):
            await environment.computer.execute(ComputerTypeText(text="permission denied first"))
        assert not events


@pytest.mark.parametrize("width,height,x,y", [(1280, 800, 747, 528), (800, 1280, 467, 844), (1, 1, 0, 0)])
async def test_relative_coordinates_use_observation_geometry_for_all_pointer_actions(width, height, x, y):
    runtime, computer = mount(COMPUTER_ACTIONS)
    original = computer.observe

    async def observe(**kwargs):
        screenshot = await original(**kwargs)
        return ComputerScreenshot(
            screenshot.observation.model_copy(update={"width": width, "height": height}), screenshot.data
        )

    computer.observe = observe
    async with runtime.bind(
        thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}
    ) as environment:
        tools = ComputerToolset(environment, coordinate_space="normalized_1000")
        image = await tools.computer_observe()
        assert image.return_value["coordinate_space"] == "normalized_1000"
        assert (await tools.computer_describe())["coordinate_space"] == "normalized_1000"
        reference = image.return_value["observation_id"]
        point = ComputerPoint(x=584, y=660)
        expected = ComputerPoint(x=x, y=y)
        assert (await tools.computer_click(reference, point))["ok"]
        assert computer.inputs[-1].point == expected
        assert (await tools.computer_move(reference, point))["ok"]
        assert computer.inputs[-1].point == expected
        assert (await tools.computer_drag(reference, ComputerPoint(x=0, y=0), ComputerPoint(x=1000, y=1000)))["ok"]
        assert computer.inputs[-1].start == ComputerPoint(x=0, y=0)
        assert computer.inputs[-1].end == ComputerPoint(x=width - 1, y=height - 1)
        assert (await tools.computer_scroll(reference, point, delta_y=7, unit="steps"))["ok"]
        assert computer.inputs[-1].point == expected
        assert computer.inputs[-1].delta_y == 7 and computer.inputs[-1].unit == "steps"


async def test_relative_coordinate_rejection_and_reference_recovery_never_dispatch():
    runtime, computer = mount(COMPUTER_ACTIONS)
    async with runtime.bind(
        thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}
    ) as environment:
        tools = ComputerToolset(environment, coordinate_space="normalized_1000")
        reference = (await tools.computer_observe()).return_value["observation_id"]
        invalid = ComputerPoint(x=1001, y=500)
        for result in (
            await tools.computer_click(reference, invalid),
            await tools.computer_move(reference, invalid),
            await tools.computer_drag(reference, ComputerPoint(x=0, y=0), invalid),
            await tools.computer_scroll(reference, invalid),
        ):
            assert not result["ok"]
            assert result["error"]["details"]["dispatch_stage"] == "pre_dispatch"
        assert computer.inputs == []
        # A new Run cannot reuse an earlier Run's geometry, regardless of coordinate mode.
        new_tools = ComputerToolset(environment, coordinate_space="normalized_1000")
        assert not (await new_tools.computer_click(reference, ComputerPoint(x=500, y=500)))["ok"]
        assert computer.inputs == []
        pixels = ComputerToolset(environment)
        observed = await pixels.computer_observe()
        assert observed.return_value["coordinate_space"] == "pixels"
        assert (await pixels.computer_click(observed.return_value["observation_id"], ComputerPoint(x=500, y=400)))["ok"]
        assert computer.inputs[-1].point == ComputerPoint(x=500, y=400)


@pytest.mark.parametrize(
    "mode,region,expected",
    [
        ("pixels", (320, 120, 640, 480), (320, 120, 640, 480)),
        ("normalized_1000", (501, 251, 1000, 1000), (320, 120, 640, 480)),
        ("normalized_1000", (0, 0, 1, 1), (0, 0, 1, 1)),
    ],
)
async def test_zoom_keeps_full_frame_reference_and_converts_exclusive_edges(mode, region, expected):
    runtime, computer = mount(COMPUTER_ACTIONS)
    async with runtime.bind(thread_id="t", run_id="r", instance=_instance(), host_refs={}) as environment:
        tools = ComputerToolset(environment, coordinate_space=mode)
        reference = (await tools.computer_observe()).return_value["observation_id"]
        box = ComputerRegion(**dict(zip(("left", "top", "right", "bottom"), region, strict=True)))
        for _ in range(20):
            result = await tools.computer_zoom(reference, box)
            assert isinstance(result, ToolReturn)
            assert result.return_value["source_observation_id"] == reference
            assert "observation_id" not in result.return_value
            assert result.return_value["coordinate_space"] == mode
            assert tuple(result.return_value["region_pixels"].values()) == expected
            assert result.metadata == {"a13n.computer.screenshot": True}
            assert result.content[0].data == b"detail-image"
        assert len(tools._observations) == 1
        assert tuple(computer.zooms[-1][1].model_dump().values()) == expected
        clicked = await tools.computer_click(reference, ComputerPoint(x=100, y=100))
        assert clicked["ok"]
        assert computer.inputs[-1].point == (
            ComputerPoint(x=64, y=48) if mode == "normalized_1000" else ComputerPoint(x=100, y=100)
        )
        assert computer.inputs[-1].observation.width == 640
        assert computer.inputs[-1].observation.height == 480


async def test_zoom_permission_binding_and_invalid_regions_reject_before_provider():
    region = ComputerRegion(left=0, top=0, right=640, bottom=480)
    runtime, computer = mount(frozenset({EnvironmentAction.COMPUTER_OBSERVE}))
    async with runtime.bind(thread_id="t", run_id="r", instance=_instance(), host_refs={}) as environment:
        tools = ComputerToolset(environment)
        reference = (await tools.computer_observe()).return_value["observation_id"]
        assert "computer_zoom" not in tools.get_toolset().tools
        assert not (await tools.computer_zoom(reference, region))["ok"]
        assert not computer.zooms
    runtime, computer = mount(COMPUTER_ACTIONS)
    async with runtime.bind(thread_id="t", run_id="r", instance=_instance(), host_refs={}) as environment:
        tools = ComputerToolset(environment, coordinate_space="normalized_1000")
        reference = (await tools.computer_observe()).return_value["observation_id"]
        assert not (await tools.computer_zoom(reference, ComputerRegion(left=0, top=0, right=1001, bottom=1)))["ok"]
        assert not (await tools.computer_zoom(reference, ComputerRegion(left=1, top=0, right=1, bottom=1)))["ok"]
        observation = tools._observations[reference]
        for update in ({"execution_id": "missing"}, {"observed_generation": "old"}):
            with pytest.raises(EnvironmentError):
                await environment.computer.zoom(observation.model_copy(update=update), region)
        with pytest.raises(EnvironmentError):
            await environment.computer.zoom(observation, region, max_dimension=2049)
        assert not computer.zooms
        assert not (await ComputerToolset(environment).computer_zoom(reference, region))["ok"]


@pytest.mark.parametrize(
    ("methods", "expected"),
    [
        (("computer.observe", "computer.close_observation"), {EnvironmentAction.COMPUTER_OBSERVE}),
        (("computer.zoom",), set()),
        (("computer.zoom", "computer.close_observation"), {EnvironmentAction.COMPUTER_ZOOM}),
        (
            ("computer.observe", "computer.zoom", "computer.close_observation"),
            {EnvironmentAction.COMPUTER_OBSERVE, EnvironmentAction.COMPUTER_ZOOM},
        ),
    ],
)
def test_eip_zoom_advertisement_requires_exact_method_and_reader_cleanup(methods, expected):
    from a13n_envd_client.eip import v1 as eip
    from a13n_harness.providers.environment.eip.binding import _convert_descriptor

    descriptor = eip.SessionDescriptor(
        boundary=eip.ExecutionBoundary.model_validate(
            {
                "sandbox": {"mode": "disabled"},
                "egress": "inherit",
                "privilege_gain_blocked": False,
                "backend": "native",
                "policy_digest": "a" * 64,
            }
        ),
        device_id="device-zoom",
        session_id="ses-zoom",
        generation=1,
        working_directory="/",
        lifecycle=eip.SessionLifecyclePolicy(idle_timeout_ms=30000, disconnect_grace_ms=1000),
        available_methods=methods,
        limits=eip.EIPLimits(
            max_request_bytes=1024,
            max_response_bytes=1024,
            max_concurrent_operations=4,
            max_processes=1,
            max_operation_duration_ms=1000,
            max_output_preview_bytes=1,
            max_output_bytes_per_stream=1,
            max_transfer_frame_bytes=128,
            max_concurrent_file_transfers=2,
            max_file_transfer_bytes=1024,
            max_file_bytes=1024,
        ),
        execution_features=eip.ExecutionFeatures(
            process_count_limit=False,
            memory_bytes_limit=False,
            cpu_time_limit=False,
            signal_interrupt=False,
            signal_terminate=False,
        ),
    )
    assert _convert_descriptor(descriptor).permissions.operations == expected
