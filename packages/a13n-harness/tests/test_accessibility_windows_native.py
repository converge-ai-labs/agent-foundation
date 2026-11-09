"""Opt-in WPF -> UIA helper -> Session/EIP -> Harness native acceptance.

Requires the disposable, ordinary-user Windows desktop described in
 dev/fixtures/windows-desktop/README.md. No hypervisor input or elevation.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from a13n_envd_client import EIPDeviceConnection, HttpTransport
from a13n_envd_client.eip import v1 as eip
from a13n_envd_client.errors import EIPMethodError
from a13n_harness.environment import EnvironmentMount
from a13n_harness.environment.accessibility import (
    AccessibilityLimits,
    AccessibilityRangeAcquire,
    AccessibilityRangeFind,
    AccessibilityRangeRead,
    AccessibilityRootScope,
    AccessibilityTextRead,
)
from a13n_harness.environment.accessibility_mutations import UIARequest
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.environment.models import COMPUTER_ACTIONS, EnvironmentError, EnvironmentPermissionSet
from a13n_harness.providers.environment.eip.binding import EIPEnvironmentSession

from .test_environment_core import _Binding, _instance

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(os.environ.get("A13N_UIA_TEST") != "1", reason="Requires a disposable Windows desktop"),
]


def context():
    return eip.EIPCallContext(operation_id="uia-" + uuid.uuid4().hex)


async def state_until(session, path, predicate):
    last = None
    try:
        async with asyncio.timeout(20):
            while True:
                try:
                    async with session.open_reader(eip.EIPPath(path=path)) as reader:
                        last = json.loads(b"".join([chunk async for chunk in reader]))
                    if predicate(last):
                        return last
                except (EIPMethodError, json.JSONDecodeError):
                    pass
                await asyncio.sleep(0.05)
    except TimeoutError:
        pytest.fail(f"Windows accessibility fixture did not converge: {last!r}")


@asynccontextmanager
async def desktop():
    directory = os.environ["A13N_WINDOWS_DIRECTORY"].rstrip("/")
    device = await EIPDeviceConnection.initialize(
        HttpTransport(
            endpoint=os.environ["A13N_WINDOWS_ENDPOINT"],
            credential=Path(os.environ["A13N_WINDOWS_CREDENTIAL_FILE"]).read_text().strip(),
        ),
        expected_device_id=os.environ["A13N_WINDOWS_DEVICE_ID"],
    )
    async with device, await device.open_session() as session:
        # A unique file pair prevents old fixture evidence from satisfying readiness.
        stem = "accessibility-" + uuid.uuid4().hex
        script = directory + "/" + stem + ".ps1"
        state = directory + "/" + stem + ".json"
        source = Path(__file__).resolve().parents[3] / "dev/fixtures/windows-desktop/accessibility.ps1"
        async with session.open_writer(eip.EIPPath(path=script), mode="create") as writer:
            await writer.write(source.read_bytes())
            await writer.commit()
        native_script = script.removeprefix("/").replace("/", "\\").replace("'", "''")
        native_state = state.removeprefix("/").replace("/", "\\").replace("'", "''")
        await session.client.process_start(
            eip.ProcessStartParams(
                context=context(),
                request=eip.CommandRequest(
                    command=eip.ShellCommand(
                        kind="shell",
                        profile_id="default",
                        script=f"powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File '{native_script}' -StatePath '{native_state}'",
                    )
                ),
            )
        )
        initial = await state_until(session, state, lambda value: value["ready"])
        yield session, state, initial["pid"]


async def test_real_windows_accessibility_session_and_harness():
    async with desktop() as (session, state, pid):
        adapter = EIPEnvironmentSession(
            session=session, provider_key="native-test", environment_id="desktop", execution_id="exec-uia"
        )
        binding = _Binding(
            "desktop", families=frozenset({"computer"}), operations=adapter.operations, permissions=COMPUTER_ACTIONS
        )
        binding.bound.execution_id = "exec-uia"
        binding.bound.descriptor = adapter.descriptor
        mount = EnvironmentMount(
            source=binding, permission_ceiling=EnvironmentPermissionSet(operations=COMPUTER_ACTIONS)
        )
        runtime = create_environment_runtime(mounts={"desktop": mount}, default_mount="desktop")
        async with runtime.bind(thread_id="thread-uia", run_id="run-uia", instance=_instance(), host_refs={}) as env:
            a11y = env.computer.accessibility
            description = await a11y.describe()
            assert description.ready and description.backend == "uia"
            assert len(description.methods) == 39
            limits = AccessibilityLimits(depth=12, max_native_calls=8192)
            roots = await a11y.roots(AccessibilityRootScope(kind="application", process_id=pid), limits=limits)
            root = next(node.reference for node in roots.nodes if node.name == "Envd accessibility fixture")
            nodes = []
            cursor = None
            for _ in range(128):
                page = await a11y.inspect(root, view="raw", cursor=cursor, limits=limits)
                nodes.extend(page.nodes)
                cursor = page.completeness.continuation
                if cursor is None:
                    break
            else:
                pytest.fail("UIA traversal did not finish")
            named = {node.name: node for node in nodes if node.name}
            assert all(node.image_projection is None for node in nodes)
            assert all(node.parent is not None for node in nodes[1:])

            async def execute(name, request):
                result = await a11y.execute(named[name].reference, UIARequest(request=request), readback=True)
                assert result.effect == "executed" and result.native_status == "returned_success", result
                assert result.receipt.stage == "completed" and result.application_outcome == "unverified"
                assert result.observation is None or result.observation.reference == named[name].reference
                return result

            await execute("Apply changes", {"op": "invoke"})
            await state_until(session, state, lambda value: value["clicks"] == 1)
            await execute("Unicode editor", {"op": "value_set", "value": "Hello 世界😀"})
            await state_until(session, state, lambda value: value["text"] == "Hello 世界😀")
            content = await a11y.read(named["Unicode editor"].reference, AccessibilityTextRead(), limits=limits)
            assert content.text.text == "Hello 世界😀"
            await execute("Enable feature", {"op": "toggle"})
            await state_until(session, state, lambda value: value["checked"])
            await execute("Numeric value", {"op": "range_value_set", "value": 42})
            await state_until(session, state, lambda value: value["numeric"] == 42)
            await execute("Details", {"op": "expand"})
            await state_until(session, state, lambda value: value["expanded"])
            await execute("Details", {"op": "collapse"})
            await execute("Beta", {"op": "selection_select"})
            await state_until(session, state, lambda value: value["selection"] == ["Beta"])
            for name, request in (
                ("Read-only editor", {"op": "value_set", "value": "forbidden"}),
                ("Protected editor", {"op": "value_set", "value": "forbidden"}),
                ("Disabled action", {"op": "invoke"}),
                ("Numeric value", {"op": "range_value_set", "value": 101}),
            ):
                with pytest.raises(EnvironmentError):
                    await a11y.execute(named[name].reference, UIARequest(request=request))
            protected = await a11y.read(named["Protected editor"].reference, AccessibilityTextRead(), limits=limits)
            assert protected.text.status == "protected" and protected.text.text is None
            owner = named["Native document"].reference
            ranges = await a11y.range(owner, AccessibilityRangeAcquire(kind="document"), limits=limits)
            document = ranges.ranges[0]
            assert document.unit == "uia_opaque"
            found = await a11y.range(owner, AccessibilityRangeFind(text="世界😀"), source=document, limits=limits)
            text = await a11y.range_read(found.ranges[0], AccessibilityRangeRead(kind="text"), limits=limits)
            assert text.text.text == "世界😀"
            unchanged = await a11y.range_read(document, AccessibilityRangeRead(kind="text"), limits=limits)
            assert "Hello 世界😀" in unchanged.text.text
            await execute("Native document", {"op": "range_select", "range": found.ranges[0].model_dump(mode="json")})
            await state_until(session, state, lambda value: value["document_selection"] == "世界😀")
            # An opaque native range cannot be applied to another element, even
            # when both elements belong to the same Session and backend epoch.
            with pytest.raises(EnvironmentError):
                await a11y.execute(
                    named["Unicode editor"].reference,
                    UIARequest(request={"op": "range_select", "range": document.model_dump(mode="json")}),
                )
            tiny = limits.model_copy(update={"max_result_bytes": 1})
            with pytest.raises(EnvironmentError):
                await a11y.inspect(root, limits=tiny)
            assert (await a11y.read(owner, AccessibilityTextRead(), limits=limits)).text.text
