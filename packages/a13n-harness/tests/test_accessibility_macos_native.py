"""Opt-in AppKit -> AX helper -> Session/EIP -> Harness native acceptance.

See dev/fixtures/macos-desktop/README.md. Run serially on a disposable login
session with Accessibility permission granted through normal System Settings.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
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
from a13n_harness.environment.accessibility_mutations import AXRequest
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.environment.models import COMPUTER_ACTIONS, EnvironmentError, EnvironmentPermissionSet
from a13n_harness.providers.environment.eip.binding import EIPEnvironmentSession

from .test_environment_core import _Binding, _instance

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(os.environ.get("A13N_AX_TEST") != "1", reason="Requires a disposable macOS login session"),
]


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
        pytest.fail(f"AppKit fixture did not converge: {last!r}")


@asynccontextmanager
async def desktop():
    directory = os.environ["A13N_MACOS_DIRECTORY"].rstrip("/")
    device = await EIPDeviceConnection.initialize(
        HttpTransport(
            endpoint=os.environ["A13N_MACOS_ENDPOINT"],
            credential=Path(os.environ["A13N_MACOS_CREDENTIAL_FILE"]).read_text().strip(),
        ),
        expected_device_id=os.environ["A13N_MACOS_DEVICE_ID"],
    )
    async with device, await device.open_session() as session:
        state = directory + "/accessibility-" + uuid.uuid4().hex + ".json"
        # Compile once on the Mac before starting the test. The authenticated
        # Session owns the fixture process and stops it on Session teardown.
        fixture = shlex.quote(directory + "/accessibility-fixture")
        await session.client.process_start(
            eip.ProcessStartParams(
                context=eip.EIPCallContext(operation_id="ax-fixture-" + uuid.uuid4().hex),
                request=eip.CommandRequest(
                    command=eip.ShellCommand(
                        kind="shell", profile_id="default", script=f"exec {fixture} {shlex.quote(state)}"
                    )
                ),
            )
        )
        initial = await state_until(session, state, lambda value: value["ready"])
        yield session, state, initial["pid"]


async def test_real_macos_accessibility_session_and_harness():
    async with desktop() as (session, state, pid):
        adapter = EIPEnvironmentSession(
            session=session, provider_key="native-test", environment_id="desktop", execution_id="exec-ax"
        )
        binding = _Binding(
            "desktop", families=frozenset({"computer"}), operations=adapter.operations, permissions=COMPUTER_ACTIONS
        )
        binding.bound.execution_id = "exec-ax"
        binding.bound.descriptor = adapter.descriptor
        mount = EnvironmentMount(
            source=binding, permission_ceiling=EnvironmentPermissionSet(operations=COMPUTER_ACTIONS)
        )
        runtime = create_environment_runtime(mounts={"desktop": mount}, default_mount="desktop")
        async with runtime.bind(thread_id="thread-ax", run_id="run-ax", instance=_instance(), host_refs={}) as env:
            a11y = env.computer.accessibility
            description = await a11y.describe()
            assert description.ready and description.backend == "ax", description
            assert len(description.methods) == 26
            limits = AccessibilityLimits(depth=12, max_native_calls=8192)
            roots = await a11y.roots(AccessibilityRootScope(kind="application", process_id=pid), limits=limits)
            root = roots.nodes[0].reference
            nodes = []
            cursor = None
            for _ in range(128):
                page = await a11y.inspect(root, view="raw", cursor=cursor, limits=limits)
                nodes.extend(page.nodes)
                cursor = page.completeness.continuation
                if cursor is None:
                    break
            else:
                pytest.fail("AX traversal did not finish")
            named = {node.identifier: node for node in nodes if node.identifier}
            assert all(node.image_projection is None for node in nodes)
            assert all(node.parent is not None for node in nodes[1:])

            async def execute(identifier, request):
                node = named[identifier].reference
                result = await a11y.execute(node, AXRequest(request=request), readback=True)
                assert result.effect == "executed" and result.native_status == "returned_success", result
                assert result.receipt.stage == "completed" and result.application_outcome == "unverified"
                assert result.observation is None or result.observation.reference == node
                return result

            press = next(
                capability.action
                for capability in named["fixture-apply"].capabilities
                if capability.action and capability.action.native_identifier == "AXPress"
            )
            await execute("fixture-apply", {"op": "perform_action", "action": press.model_dump(mode="json")})
            await state_until(session, state, lambda value: value["clicks"] == 1)
            await execute("fixture-editor", {"op": "value_string_set", "value": "Hello 世界😀"})
            await state_until(session, state, lambda value: value["text"] == "Hello 世界😀")
            content = await a11y.read(named["fixture-editor"].reference, AccessibilityTextRead(), limits=limits)
            assert content.text.text == "Hello 世界😀"
            await execute("fixture-numeric", {"op": "value_number_set", "value": 42})
            await state_until(session, state, lambda value: value["numeric"] == 42)
            for identifier, request in (
                ("fixture-readonly", {"op": "value_string_set", "value": "forbidden"}),
                ("fixture-protected", {"op": "value_string_set", "value": "forbidden"}),
                ("fixture-disabled", {"op": "focus"}),
                ("fixture-numeric", {"op": "value_number_set", "value": 101}),
                ("fixture-editor", {"op": "perform_action", "action": press.model_dump(mode="json")}),
            ):
                with pytest.raises(EnvironmentError):
                    await a11y.execute(named[identifier].reference, AXRequest(request=request))
            protected = await a11y.read(named["fixture-protected"].reference, AccessibilityTextRead(), limits=limits)
            assert protected.text.status == "protected" and protected.text.text is None
            owner = named["fixture-document"].reference
            ranges = await a11y.range(owner, AccessibilityRangeAcquire(kind="document"), limits=limits)
            document = ranges.ranges[0]
            assert document.unit == "ax_native"
            found = await a11y.range(owner, AccessibilityRangeFind(text="世界😀"), source=document, limits=limits)
            text = await a11y.range_read(found.ranges[0], AccessibilityRangeRead(kind="text"), limits=limits)
            assert text.text.text == "世界😀"
            assert text.text.span.end - text.text.span.start == 4
            await execute(
                "fixture-document", {"op": "text_range_select", "range": found.ranges[0].model_dump(mode="json")}
            )
            await state_until(session, state, lambda value: value["document_selection"] == "世界😀")
            with pytest.raises(EnvironmentError):
                await a11y.execute(
                    named["fixture-editor"].reference,
                    AXRequest(request={"op": "text_range_select", "range": document.model_dump(mode="json")}),
                )
            with pytest.raises(EnvironmentError):
                await a11y.inspect(root, limits=limits.model_copy(update={"max_result_bytes": 1}))
            assert (await a11y.read(owner, AccessibilityTextRead(), limits=limits)).text.text
