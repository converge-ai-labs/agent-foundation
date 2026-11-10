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
    AccessibilityFind,
    AccessibilityLimits,
    AccessibilityRangeAcquire,
    AccessibilityRangeFind,
    AccessibilityRangeRead,
    AccessibilityRangeSpan,
    AccessibilityRootScope,
    AccessibilitySimpleRead,
    AccessibilityTableRead,
    AccessibilityTextRead,
    NativePoint,
    NativeSpan,
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


async def signal_fixture(session, pid, signal):
    started = await session.client.process_start(
        eip.ProcessStartParams(
            context=eip.EIPCallContext(operation_id="ax-signal-" + uuid.uuid4().hex),
            request=eip.CommandRequest(
                command=eip.ShellCommand(kind="shell", profile_id="default", script=f"/bin/kill -{signal} {pid}")
            ),
        )
    )
    waited = await session.client.process_wait(
        eip.ProcessWaitParams(
            context=eip.EIPCallContext(operation_id="ax-wait-" + uuid.uuid4().hex, timeout_ms=5000),
            handle=started.process.handle,
            condition=eip.ProcessWaitCondition.TREE_CLEANED,
        )
    )
    assert waited.process.status.cleanup.value == "complete" and waited.process.status.exit_code == 0


async def test_real_macos_accessibility_session_and_harness(record_property):
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
            # AppKit can lazily add anonymous AXGroups between live pages.
            # Fixture controls have stable identifiers and must all survive.
            expected = {node.identifier: node.reference for node in nodes if node.identifier}
            for view in ("raw", "control", "content"):
                observed = []
                cursor = None
                for _ in range(128):
                    page = await a11y.inspect(
                        root, view=view, cursor=cursor, limits=limits.model_copy(update={"max_nodes": 3})
                    )
                    observed.extend(page.nodes)
                    cursor = page.completeness.continuation
                    if cursor is None:
                        break
                else:
                    pytest.fail(f"AX {view} pagination did not finish")
                references = [node.reference for node in observed]
                assert len(references) == len(set(references))
                assert {node.identifier: node.reference for node in observed if node.identifier} == expected
            windows = await a11y.roots(AccessibilityRootScope(kind="windows"), limits=limits)
            window = next(node for node in windows.nodes if node.name == "Envd accessibility fixture")
            named["fixture-window"] = window
            bounds = named["fixture-apply"].bounds
            hit = await a11y.roots(
                AccessibilityRootScope(
                    kind="at_point",
                    point=NativePoint(frame="ax_screen", x=bounds.x + bounds.width / 2, y=bounds.y + bounds.height / 2),
                ),
                limits=limits,
            )
            assert hit.nodes[0].reference == named["fixture-apply"].reference
            long_name = "Segment " * 40 + "Straße 世界😀 END"
            for query in (
                AccessibilityFind(name=long_name),
                AccessibilityFind(name="STRASSE 世界😀", name_match="substring_casefold"),
            ):
                found_name = await a11y.find(root, query, limits=limits.model_copy(update={"max_text_bytes": 32}))
                assert [node.identifier for node in found_name.nodes] == ["fixture-long-name"]
            assert all(node.image_projection is None for node in nodes)
            assert all(node.parent is not None for node in nodes[1:])
            with pytest.raises(EnvironmentError):
                await a11y.inspect(root, limits=limits.model_copy(update={"max_native_calls": 1}))
            small = limits.model_copy(update={"max_items": 1})
            table = await a11y.read(
                named["fixture-table"].reference,
                AccessibilityTableRead(rows=1, columns=1, include_headers=True, include_selection=True),
                limits=small,
            )
            assert len(table.properties) == 1 and table.completeness.reason == "item_limit"
            selection = await a11y.read(
                named["fixture-table"].reference, AccessibilitySimpleRead(kind="selection"), limits=limits
            )
            assert any(prop.name == "AXSelectedRows" for prop in selection.properties)
            scroll = await a11y.read(
                named["fixture-scroll"].reference, AccessibilitySimpleRead(kind="scroll"), limits=small
            )
            assert len(scroll.nodes) == 1 and scroll.completeness.reason == "item_limit"
            one_node = limits.model_copy(update={"max_nodes": 1, "max_items": 128})
            cells = await a11y.read(
                named["fixture-table"].reference,
                AccessibilityTableRead(rows=2, columns=1, include_headers=False),
                limits=one_node,
            )
            assert len(cells.nodes) == 1 and cells.completeness.reason == "node_limit"
            full_table = await a11y.read(
                named["fixture-table"].reference,
                AccessibilityTableRead(rows=2, columns=2, include_headers=False),
                limits=limits,
            )
            assert full_table.completeness.complete and len(full_table.nodes) == 4
            dimensions = {prop.name: prop.value.value for prop in full_table.properties}
            assert dimensions == {"AXRowCount": 2, "AXColumnCount": 2}
            values = []
            for cell in full_table.nodes:
                text = await a11y.read(cell.reference, AccessibilityTextRead(), limits=limits)
                values.append(text.text.text)
            assert values == ["First 0", "Second 0", "First 1", "Second 1"]
            offset = await a11y.read(
                named["fixture-table"].reference,
                AccessibilityTableRead(row=1, column=1, include_headers=False),
                limits=limits,
            )
            assert len(offset.nodes) == 1
            assert (
                await a11y.read(offset.nodes[0].reference, AccessibilityTextRead(), limits=limits)
            ).text.text == "Second 1"
            bars = await a11y.read(
                named["fixture-scroll"].reference, AccessibilitySimpleRead(kind="scroll"), limits=one_node
            )
            assert len(bars.nodes) == 1 and bars.completeness.reason == "node_limit"

            async def execute(identifier, request):
                node = named[identifier].reference
                result = await a11y.execute(node, AXRequest(request=request), readback=True)
                assert result.effect == "executed" and result.native_status == "returned_success", result
                assert result.dispatch_complete is True
                assert result.receipt.stage == "completed" and result.application_outcome == "unverified"
                assert result.observation is None or result.observation.reference == node
                return result

            press = next(
                capability.action
                for capability in named["fixture-apply"].capabilities
                if capability.action and capability.action.native_identifier == "AXPress"
            )
            await execute("fixture-apply", {"op": "perform_action", "action": press.model_dump(mode="json")})
            await state_until(session, state, lambda value: value["clicks"] == 1 and value["dynamic"])
            dynamic = await a11y.find(root, AccessibilityFind(identifier="fixture-dynamic"), limits=limits)
            assert len(dynamic.nodes) == 1
            await execute("fixture-apply", {"op": "perform_action", "action": press.model_dump(mode="json")})
            await state_until(session, state, lambda value: value["clicks"] == 2 and not value["dynamic"])
            assert not (await a11y.find(root, AccessibilityFind(identifier="fixture-dynamic"), limits=limits)).nodes
            with pytest.raises(EnvironmentError):
                await a11y.read(dynamic.nodes[0].reference, AccessibilityTextRead(), limits=limits)
            await execute("fixture-editor", {"op": "focus"})
            await state_until(session, state, lambda value: value["editor_focused"])
            focused = await a11y.inspect(
                named["fixture-editor"].reference, limits=limits.model_copy(update={"depth": 0})
            )
            assert focused.nodes[0].states["focused"] is True
            toggle_press = next(
                cap.action
                for cap in named["fixture-toggle"].capabilities
                if cap.action and cap.action.native_identifier == "AXPress"
            )
            await execute("fixture-toggle", {"op": "perform_action", "action": toggle_press.model_dump(mode="json")})
            await state_until(session, state, lambda value: value["checked"])
            toggle = await a11y.read(
                named["fixture-toggle"].reference, AccessibilitySimpleRead(kind="numeric_value"), limits=limits
            )
            assert next(prop.value.value for prop in toggle.properties if prop.name == "AXValue") == 1
            await execute("fixture-editor", {"op": "value_string_set", "value": "Hello 世界😀"})
            await state_until(session, state, lambda value: value["text"] == "Hello 世界😀")
            content = await a11y.read(named["fixture-editor"].reference, AccessibilityTextRead(), limits=limits)
            assert content.text.text == "Hello 世界😀"
            await execute("fixture-numeric", {"op": "value_number_set", "value": 42})
            await state_until(session, state, lambda value: value["numeric"] == 42)
            numeric = await a11y.read(
                named["fixture-numeric"].reference, AccessibilitySimpleRead(kind="numeric_value"), limits=limits
            )
            assert next(prop.value.value for prop in numeric.properties if prop.name == "AXValue") == 42
            rows = await a11y.find(
                named["fixture-table"].reference, AccessibilityFind(native_role="AXRow"), limits=limits
            )
            assert len(rows.nodes) == 2
            await execute(
                "fixture-table",
                {"op": "selected_rows_set", "children": [rows.nodes[1].reference.model_dump(mode="json")]},
            )
            await state_until(session, state, lambda value: value["selected_row"] == 1)
            selected = await a11y.read(
                named["fixture-table"].reference, AccessibilitySimpleRead(kind="selection"), limits=limits
            )
            assert next(prop.value.values for prop in selected.properties if prop.name == "AXSelectedRows") == (
                rows.nodes[1].reference,
            )
            await execute(
                "fixture-table",
                {"op": "selected_rows_set", "children": [row.reference.model_dump(mode="json") for row in rows.nodes]},
            )
            await state_until(session, state, lambda value: value["selected_rows"] == [0, 1])
            selected = await a11y.read(
                named["fixture-table"].reference, AccessibilitySimpleRead(kind="selection"), limits=limits
            )
            assert next(prop.value.values for prop in selected.properties if prop.name == "AXSelectedRows") == tuple(
                row.reference for row in rows.nodes
            )
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
            prefix = await a11y.range(
                owner,
                AccessibilityRangeSpan(span=NativeSpan(unit="ax_native", start=0, end=5)),
                limits=limits,
            )
            outside = await a11y.range(
                owner, AccessibilityRangeFind(text="世界😀"), source=prefix.ranges[0], limits=limits
            )
            assert outside.completeness.complete and not outside.ranges
            found = await a11y.range(owner, AccessibilityRangeFind(text="世界😀"), source=document, limits=limits)
            text = await a11y.range_read(found.ranges[0], AccessibilityRangeRead(kind="text"), limits=limits)
            assert text.text.text == "世界😀"
            assert text.text.span.end - text.text.span.start == 4
            # UTF-8 bounding must keep the suffix nearest the backward endpoint.
            backward = await a11y.range(
                owner,
                AccessibilityRangeFind(text="e", backward=True),
                source=document,
                limits=limits.model_copy(update={"max_text_bytes": 4}),
            )
            assert backward.ranges
            last = await a11y.range_read(backward.ranges[0], AccessibilityRangeRead(kind="text"), limits=limits)
            assert (
                last.text.text == "e"
                and last.text.span.end == len("Hello 世界😀\nSecond line".encode("utf-16-le")) // 2
            )
            await execute(
                "fixture-document", {"op": "text_range_select", "range": found.ranges[0].model_dump(mode="json")}
            )
            await state_until(session, state, lambda value: value["document_selection"] == "世界😀")
            selected = await a11y.range(owner, AccessibilityRangeAcquire(kind="selection"), limits=limits)
            assert (
                await a11y.range_read(selected.ranges[0], AccessibilityRangeRead(kind="text"), limits=limits)
            ).text.text == "世界😀"
            await execute("fixture-document", {"op": "selected_text_set", "text": "replacement😀"})
            await state_until(
                session, state, lambda value: value["document_text"] == "Hello replacement😀\nSecond line"
            )
            assert (
                await a11y.read(owner, AccessibilityTextRead(), limits=limits)
            ).text.text == "Hello replacement😀\nSecond line"
            with pytest.raises(EnvironmentError):
                await a11y.execute(
                    named["fixture-editor"].reference,
                    AXRequest(request={"op": "text_range_select", "range": document.model_dump(mode="json")}),
                )
            with pytest.raises(EnvironmentError):
                await a11y.inspect(root, limits=limits.model_copy(update={"max_result_bytes": 1}))
            assert (await a11y.read(owner, AccessibilityTextRead(), limits=limits)).text.text

            await execute("fixture-window", {"op": "position_set", "x": 120, "y": 140})
            await state_until(session, state, lambda value: value["window_x"] == 120 and value["window_y"] == 140)
            await execute("fixture-window", {"op": "size_set", "width": 860, "height": 680})
            await state_until(
                session, state, lambda value: value["window_width"] == 860 and value["window_height"] == 680
            )
            resized = await a11y.inspect(window.reference, limits=limits.model_copy(update={"depth": 0}))
            bounds = resized.nodes[0].bounds
            assert bounds.frame == "ax_screen"
            assert (bounds.x, bounds.y, bounds.width, bounds.height) == (120, 140, 860, 680)
            await execute("fixture-window", {"op": "main_set", "value": True})
            await state_until(session, state, lambda value: value["window_main"])
            main = await a11y.inspect(window.reference, limits=limits.model_copy(update={"depth": 0}))
            assert main.nodes[0].states["main"] is True
            for minimized in (True, False):
                await execute("fixture-window", {"op": "minimized_set", "value": minimized})
                await state_until(
                    session, state, lambda value, minimized=minimized: value["window_minimized"] == minimized
                )
                observed = await a11y.inspect(window.reference, limits=limits.model_copy(update={"depth": 0}))
                assert observed.nodes[0].states["minimized"] == minimized

            # Stall only our provider. Native AX must honor the short deadline,
            # and the same Session must recover once the provider resumes.
            await signal_fixture(session, pid, "STOP")
            started = asyncio.get_running_loop().time()
            try:
                with pytest.raises(EnvironmentError) as timeout:
                    await a11y.read(
                        owner, AccessibilityTextRead(), limits=limits.model_copy(update={"timeout_ms": 100})
                    )
                elapsed = asyncio.get_running_loop().time() - started
                record_property("stalled_read_ms", round(elapsed * 1000, 2))
                record_property("stalled_read_error", timeout.value.code)
                assert elapsed < 1, "100 ms AX read blocked near the native default timeout"
                if timeout.value.code == "environment_unavailable":
                    # AX can return CannotComplete before the supervisor expires.
                    # Preserve that exact native error rather than relabel it.
                    cause = timeout.value.__cause__
                    assert isinstance(cause, EIPMethodError)
                    assert cause.error.data.error_type == eip.ErrorType.PROVIDER_UNAVAILABLE
                    assert cause.error.data.safe_detail == "AXError -25204"
                else:
                    assert timeout.value.code == "environment_timeout", timeout.value.safe_projection()
            finally:
                await signal_fixture(session, pid, "CONT")
            recovered = await a11y.roots(AccessibilityRootScope(kind="application", process_id=pid), limits=limits)
            fresh_root = recovered.nodes[0].reference
            fresh = await a11y.find(fresh_root, AccessibilityFind(identifier="fixture-document"), limits=limits)
            assert len(fresh.nodes) == 1
            assert (
                await a11y.read(fresh.nodes[0].reference, AccessibilityTextRead(), limits=limits)
            ).text.text == "Hello replacement😀\nSecond line"
            record_property("helper_restarted", fresh_root.backend_epoch != root.backend_epoch)
            record_property(
                "window_scale", (await state_until(session, state, lambda value: value["ready"]))["window_scale"]
            )
            if fresh_root.backend_epoch != root.backend_epoch:
                with pytest.raises(EnvironmentError):
                    await a11y.read(owner, AccessibilityTextRead(), limits=limits)
            await signal_fixture(session, pid, "TERM")
            with pytest.raises(EnvironmentError):
                await a11y.read(fresh.nodes[0].reference, AccessibilityTextRead(), limits=limits)
