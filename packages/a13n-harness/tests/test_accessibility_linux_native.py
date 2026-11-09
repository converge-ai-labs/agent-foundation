"""Opt-in GTK -> AT-SPI helper -> Session/EIP -> Harness integration.

Run through dev/fixtures/linux-desktop/test-accessibility.sh with a prebuilt
A13N_ENVD_TEST_BINARY. The wrapper supplies a disposable X11 desktop/user bus.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from a13n_envd_client import EIPDeviceConnection, StdioTransport
from a13n_envd_client.eip import v1 as eip
from a13n_envd_client.errors import EIPMethodError
from a13n_harness.environment import EnvironmentMount
from a13n_harness.environment.accessibility import (
    AccessibilityLimits,
    AccessibilityRangeRead,
    AccessibilityRangeSpan,
    AccessibilityRootScope,
    AccessibilityTextRead,
    NativeSpan,
)
from a13n_harness.environment.accessibility_mutations import AtspiRequest
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.environment.computer import ComputerPoint
from a13n_harness.environment.models import COMPUTER_ACTIONS, EnvironmentPermissionSet
from a13n_harness.providers.environment.eip.binding import EIPEnvironmentSession

from .test_environment_core import _Binding, _instance

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(os.environ.get("A13N_ATSPI_TEST") != "1", reason="Requires the isolated AT-SPI test wrapper"),
]


async def wait_until(predicate):
    async with asyncio.timeout(15):
        while not predicate():
            await asyncio.sleep(0.02)


@asynccontextmanager
async def child(*args, **kwargs):
    process = await asyncio.create_subprocess_exec(*args, stderr=asyncio.subprocess.PIPE, **kwargs)
    try:
        yield process
    finally:
        if process.returncode is None:
            process.terminate()
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), 5)
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise
        assert process.returncode in (0, -15), stderr.decode(errors="replace")


async def test_real_linux_accessibility_session_and_harness(tmp_path):
    binary = Path(os.environ["A13N_ENVD_TEST_BINARY"])
    assert binary.is_file()
    fixture = Path(__file__).resolve().parents[3] / "dev/fixtures/linux-desktop/accessibility.py"
    state = tmp_path / "application.json"
    config = tmp_path / "envd.json"
    config.write_text(json.dumps({"device_id": "atspi-e2e", "computer_use": True}))
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "LANG", "DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"}
    }
    environment.update(A13N_ENVD_RUNTIME_DIR=str(tmp_path / "runtime"), A13N_ENVD_STATE_DIR=str(tmp_path / "state"))
    async with (
        child("/usr/bin/python3", str(fixture), str(state), stdout=asyncio.subprocess.DEVNULL) as application,
        child(
            str(binary),
            "--config",
            str(config),
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        ) as daemon,
    ):
        await wait_until(lambda: state.exists() and json.loads(state.read_text())["ready"])
        device = await EIPDeviceConnection.initialize(
            StdioTransport.from_process(daemon), expected_device_id="atspi-e2e", request_timeout=20
        )
        try:
            async with await device.open_session(
                required_methods=("computer.a11y_roots", "computer.atspi_do_action")
            ) as session:
                adapter = EIPEnvironmentSession(
                    session=session, provider_key="native-test", environment_id="desktop", execution_id="exec-native"
                )
                # Reuse only the test connector lifecycle; all operations and
                # descriptors below come from the real negotiated EIP Session.
                binding = _Binding(
                    "desktop",
                    families=frozenset({"computer"}),
                    operations=adapter.operations,
                    permissions=COMPUTER_ACTIONS,
                )
                binding.bound.execution_id = "exec-native"
                binding.bound.descriptor = adapter.descriptor
                mount = EnvironmentMount(
                    source=binding, permission_ceiling=EnvironmentPermissionSet(operations=COMPUTER_ACTIONS)
                )
                runtime = create_environment_runtime(mounts={"desktop": mount}, default_mount="desktop")
                async with runtime.bind(
                    thread_id="thread-native", run_id="run-native", instance=_instance(), host_refs={}
                ) as env:
                    accessibility = env.computer.accessibility
                    description = await accessibility.describe()
                    assert description.ready and description.backend == "atspi"
                    assert len(description.methods) == 37
                    scope = AccessibilityRootScope(kind="application", process_id=application.pid)
                    limits = AccessibilityLimits(depth=8, max_native_calls=8192)
                    roots = None
                    async with asyncio.timeout(15):
                        while roots is None or not roots.nodes:
                            roots = await accessibility.roots(scope, limits=limits)
                            if not roots.nodes:
                                await asyncio.sleep(0.02)
                    root = roots.nodes[0].reference
                    nodes = []
                    cursor = None
                    for _ in range(256):
                        tree = await accessibility.inspect(root, limits=limits, view="raw", cursor=cursor)
                        nodes.extend(tree.nodes)
                        cursor = tree.completeness.continuation
                        if cursor is None:
                            break
                    else:
                        pytest.fail("Native traversal did not finish")
                    assert all(node.image_projection is None for node in nodes)
                    named = {node.name: node for node in nodes if node.name}
                    button = named["Apply changes"]
                    action = next(capability.action for capability in button.capabilities if capability.action)
                    result = await accessibility.execute(
                        button.reference, AtspiRequest(request={"op": "do_action", "action": action}), readback=True
                    )
                    assert result.effect == "executed" and result.dispatch_complete
                    assert result.native_status == "returned_success"
                    assert result.receipt.stage == "completed" and result.application_outcome == "unverified"
                    assert result.observation is None or result.observation.reference == button.reference
                    await wait_until(lambda: json.loads(state.read_text())["clicks"] == 1)
                    editor = named["Unicode editor"].reference
                    await accessibility.execute(
                        editor, AtspiRequest(request={"op": "text_contents_set", "text": "Hello 世界😀"})
                    )
                    await wait_until(lambda: json.loads(state.read_text())["text"] == "Hello 世界😀")
                    content = await accessibility.read(editor, AccessibilityTextRead(), limits=limits)
                    assert content.text is not None, content.model_dump_json()
                    assert content.text.text == "Hello 世界😀"
                    ranges = await accessibility.range(
                        editor,
                        AccessibilityRangeSpan(span=NativeSpan(unit="atspi_character", start=6, end=9)),
                        limits=limits,
                    )
                    text = await accessibility.range_read(
                        ranges.ranges[0], AccessibilityRangeRead(kind="text"), limits=limits
                    )
                    assert text.text.text == "世界😀"
                    protected = await accessibility.read(
                        named["Protected editor"].reference, AccessibilityTextRead(), limits=limits
                    )
                    assert protected.text.status == "protected" and protected.text.text is None
                    # Projection and hit testing share an explicit retained
                    # screenshot Basis; semantic reads above captured nothing.
                    screenshot = await env.computer.observe(max_dimension=640)
                    assert screenshot.data.startswith(b"\xff\xd8")
                    projected = await accessibility.inspect(
                        root, limits=limits, view="raw", projection=screenshot.observation
                    )
                    projected_button = next(node for node in projected.nodes if node.name == "Apply changes")
                    rectangle = projected_button.image_projection
                    assert rectangle.status == "projected"
                    assert rectangle.source_observation_id == screenshot.observation.observation_id
                    hit = await accessibility.roots_at_point(
                        screenshot.observation,
                        ComputerPoint(
                            x=int((rectangle.left + rectangle.right) / 2), y=int((rectangle.top + rectangle.bottom) / 2)
                        ),
                        limits=limits,
                    )
                    assert any(node.reference == button.reference for node in hit.nodes), hit.model_dump_json()
                    assert projected_button.bounds == button.bounds
                    # No result pages are allowed to exceed the final public
                    # envelope or lose nodes when provenance/projection is added.
                    # The reserved provenance envelope must also hold one rich
                    # native node; smaller budgets may legitimately return quota.
                    paged_limits = limits.model_copy(update={"max_result_bytes": 16000})
                    cursor = None
                    seen = set()
                    for _ in range(256):
                        page = await accessibility.inspect(
                            root, limits=paged_limits, view="raw", cursor=cursor, projection=screenshot.observation
                        )
                        assert len(page.model_dump_json().encode()) <= paged_limits.max_result_bytes
                        for node in page.nodes:
                            assert node.reference.node_handle not in seen
                            seen.add(node.reference.node_handle)
                        cursor = page.completeness.continuation
                        if cursor is None:
                            break
                    else:
                        pytest.fail("Native traversal did not finish")
                    assert seen == {node.reference.node_handle for node in nodes}
                    wire_root = eip.AccessibilityNodeReference.model_validate(
                        root.model_dump(exclude={"execution_id", "observed_generation"})
                    )
                    # An unfit first node returns quota, never an empty cursor
                    # page that repeats forever. The next ordinary read survives.
                    with pytest.raises(EIPMethodError) as tiny:
                        await session.client.computer_a11y_inspect(
                            eip.ComputerA11yInspectParams(
                                context=eip.EIPCallContext(operation_id="tiny-page"),
                                node=wire_root,
                                limits=eip.AccessibilityLimits(max_result_bytes=1),
                            )
                        )
                    assert tiny.value.error.data.error_type == "quota_exceeded", tiny.value.error.model_dump_json()
                    assert (await accessibility.inspect(root, limits=limits)).nodes
                    async with await device.open_session() as other:
                        with pytest.raises(EIPMethodError) as error:
                            await other.client.computer_a11y_inspect(
                                eip.ComputerA11yInspectParams(
                                    context=eip.EIPCallContext(operation_id="foreign-node"),
                                    node=eip.AccessibilityNodeReference.model_validate(
                                        {
                                            key: value
                                            for key, value in root.model_dump().items()
                                            if key not in {"execution_id", "observed_generation"}
                                        }
                                    ),
                                )
                            )
                        assert error.value.error.data.error_type == "conflict", error.value.error.model_dump_json()
                        assert "stale" in str(error.value).lower()
                await adapter.close()
        finally:
            await device.close()
