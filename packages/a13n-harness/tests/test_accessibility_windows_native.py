"""Opt-in WPF -> UIA helper -> Session/EIP -> Harness native acceptance.

Requires the disposable, ordinary-user Windows desktop described in
 dev/fixtures/windows-desktop/README.md. No hypervisor input or elevation.
"""

from __future__ import annotations

import asyncio
import base64
import io
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
    AccessibilityFind,
    AccessibilityLimits,
    AccessibilityPropertyRead,
    AccessibilityRangeAcquire,
    AccessibilityRangeCompare,
    AccessibilityRangeFind,
    AccessibilityRangeMove,
    AccessibilityRangePoint,
    AccessibilityRangeRead,
    AccessibilityRootScope,
    AccessibilitySimpleRead,
    AccessibilityTextRead,
    NativePoint,
)
from a13n_harness.environment.accessibility_mutations import UIARequest
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.environment.computer import ComputerClick, ComputerPoint
from a13n_harness.environment.models import COMPUTER_ACTIONS, EnvironmentError, EnvironmentPermissionSet
from a13n_harness.providers.environment.eip.binding import EIPEnvironmentSession
from PIL import Image

from .test_environment_core import _Binding, _instance

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(os.environ.get("A13N_UIA_TEST") != "1", reason="Requires a disposable Windows desktop"),
]


def context():
    return eip.EIPCallContext(operation_id="uia-" + uuid.uuid4().hex)


async def state_until(session, path, predicate, *, process=None):
    last = None
    try:
        async with asyncio.timeout(20):
            while True:
                if process is not None:
                    inspected = await session.client.process_inspect(
                        eip.ProcessInspectParams(context=context(), handle=process.handle)
                    )
                    if inspected.process.status.phase != "running":
                        output = inspected.process.output.stderr.preview.data
                        stderr = base64.b64decode(output + "=" * (-len(output) % 4)).decode("utf-8", errors="replace")
                        pytest.fail(f"Windows fixture exited: {inspected.process.status}; stderr: {stderr}")
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
async def desktop(*, extended=False):
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
        extended_argument = " -Extended" if extended else ""
        started = await session.client.process_start(
            eip.ProcessStartParams(
                context=context(),
                request=eip.CommandRequest(
                    command=eip.ShellCommand(
                        kind="shell",
                        profile_id="default",
                        script=f"powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File '{native_script}' -StatePath '{native_state}'{extended_argument}",
                    )
                ),
            )
        )
        initial = await state_until(session, state, lambda value: value["ready"], process=started.process)
        yield session, state, initial["pid"]


@asynccontextmanager
async def harness(session):
    adapter = EIPEnvironmentSession(
        session=session, provider_key="native-test", environment_id="desktop", execution_id="exec-uia"
    )
    binding = _Binding(
        "desktop", families=frozenset({"computer"}), operations=adapter.operations, permissions=COMPUTER_ACTIONS
    )
    binding.bound.execution_id = "exec-uia"
    binding.bound.descriptor = adapter.descriptor
    mount = EnvironmentMount(source=binding, permission_ceiling=EnvironmentPermissionSet(operations=COMPUTER_ACTIONS))
    runtime = create_environment_runtime(mounts={"desktop": mount}, default_mount="desktop")
    async with runtime.bind(thread_id="thread-uia", run_id="run-uia", instance=_instance(), host_refs={}) as env:
        yield env


async def inspect_all(a11y, root, *, limits, view="control"):
    nodes = []
    cursor = None
    for _ in range(128):
        page = await a11y.inspect(root, view=view, cursor=cursor, limits=limits)
        nodes.extend(page.nodes)
        cursor = page.completeness.continuation
        if cursor is None:
            assert page.completeness.complete, page.completeness
            handles = [node.reference.node_handle for node in nodes]
            assert len(handles) == len(set(handles))
            return nodes
    pytest.fail("UIA traversal did not finish")


def named_controls(nodes):
    # Raw view includes a button's content TextBlock with the same name. Keep
    # the outer control encountered first, not its non-actionable text child.
    named = {}
    for node in nodes:
        if node.name:
            named.setdefault(node.name, node)
    return named


async def fixture_root(a11y, pid):
    roots = await a11y.roots(
        AccessibilityRootScope(kind="application", process_id=pid), limits=AccessibilityLimits(max_native_calls=8192)
    )
    assert roots.completeness.complete
    return next(node.reference for node in roots.nodes if node.name == "Envd accessibility fixture")


async def acknowledged(a11y, node, request):
    result = await a11y.execute(node, UIARequest(request=request), readback=True)
    assert result.effect == "executed" and result.native_status == "returned_success", result
    assert result.dispatch_complete
    assert result.receipt.stage == "completed" and result.application_outcome == "unverified"
    if request["op"] != "window_close":
        assert result.observation is not None and result.observation.reference == node
    else:
        assert result.observation is None
    return result


async def test_real_windows_accessibility_session_and_harness():
    async with desktop() as (session, state, pid):
        async with harness(session) as env:
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
            named = named_controls(nodes)
            assert named["Apply changes"].identifier == "apply-changes"
            assert named["Apply changes"].role == "button"
            assert all(node.image_projection is None for node in nodes)
            assert all(node.parent is not None for node in nodes[1:])

            # References are view-independent: a raw-only container remains
            # the exact scope boundary when traversed through a filtered view.
            for view in ("control", "content"):
                scoped = []
                scoped_cursor = None
                for _ in range(32):
                    page = await a11y.inspect(
                        named["Raw-only boundary"].reference, view=view, cursor=scoped_cursor, limits=limits
                    )
                    scoped.extend(page.nodes)
                    scoped_cursor = page.completeness.continuation
                    if scoped_cursor is None:
                        assert page.completeness.complete
                        break
                else:
                    pytest.fail(f"Scoped UIA {view} traversal did not finish")
                scoped_names = {node.name for node in scoped if node.name}
                assert "Scoped child" in scoped_names
                assert scoped_names <= {"Raw-only boundary", "Scoped child"}

            # Output truncation must not change exact or suffix predicates.
            query_limits = AccessibilityLimits(depth=0, max_text_bytes=4, max_native_calls=8192)
            for query, expected in (
                (AccessibilityFind(name="Appl"), False),
                (AccessibilityFind(name="Apply changes"), True),
                (AccessibilityFind(name="CHANGES", name_match="substring_casefold"), True),
                (AccessibilityFind(identifier="appl"), False),
                (AccessibilityFind(identifier="apply-changes"), True),
            ):
                page = await a11y.find(named["Apply changes"].reference, query, limits=query_limits)
                assert bool(page.nodes) == expected
                assert page.completeness.reason == "byte_limit"
                if expected:
                    assert page.nodes[0].name == "Appl"
                    assert page.nodes[0].identifier == "appl"

            async def execute(name, request):
                return await acknowledged(a11y, named[name].reference, request)

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
            await state_until(session, state, lambda value: not value["expanded"])
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
            # Derivation must outlive the registry's per-kind capacity. Each
            # request pins its source while old, unused ranges are reclaimed.
            source = document
            for _ in range(300):
                derived = await a11y.range(owner, AccessibilityRangeFind(text="世界😀"), source=source, limits=limits)
                assert len(derived.ranges) == 1
                source = derived.ranges[0]
            text = await a11y.range_read(source, AccessibilityRangeRead(kind="text"), limits=limits)
            assert text.text.text == "世界😀"


async def test_real_windows_projection_and_additional_mutations():
    async with desktop() as (session, state, pid), harness(session) as env:
        a11y = env.computer.accessibility
        limits = AccessibilityLimits(depth=12, max_native_calls=8192)
        root = await fixture_root(a11y, pid)
        named = named_controls(await inspect_all(a11y, root, limits=limits, view="raw"))
        marker = named["Projection marker"].reference
        current = await state_until(session, state, lambda value: value["ready"])
        for size in (1280, 768):
            visible = await a11y.roots(
                AccessibilityRootScope(
                    kind="at_point",
                    point=NativePoint(
                        frame="uia_physical_screen",
                        x=current["marker_x"] + current["marker_width"] * current["dpi_x"] / 2,
                        y=current["marker_y"] + current["marker_height"] * current["dpi_x"] / 2,
                    ),
                ),
                limits=limits,
            )
            assert visible.nodes and visible.nodes[0].reference == marker, ("Fixture marker is occluded", current)
            screenshot = await env.computer.observe(max_dimension=size)
            sample = (
                await a11y.inspect(marker, limits=AccessibilityLimits(depth=0), projection=screenshot.observation)
            ).nodes[0]
            bounds = sample.bounds
            assert bounds.frame == "uia_physical_screen"
            assert bounds.x == pytest.approx(current["marker_x"], abs=1)
            assert bounds.y == pytest.approx(current["marker_y"], abs=1)
            assert bounds.width == pytest.approx(current["marker_width"] * current["dpi_x"], abs=1)
            projection = sample.image_projection
            assert projection.status == "projected"
            point = ComputerPoint(
                x=round((projection.left + projection.right) / 2), y=round((projection.top + projection.bottom) / 2)
            )
            # Inspect only the fixture's solid-color target, never unrelated pixels.
            with Image.open(io.BytesIO(screenshot.data)) as image:
                pixel = image.convert("RGB").getpixel((point.x, point.y))
            assert all(abs(actual - expected) <= 12 for actual, expected in zip(pixel, (23, 185, 106), strict=True)), (
                pixel,
                size,
                bounds,
                projection,
                current,
                screenshot.observation,
            )
            hit = await a11y.roots_at_point(screenshot.observation, point, limits=limits)
            assert hit.nodes[0].reference == marker
            native_hit = await a11y.roots(
                AccessibilityRootScope(
                    kind="at_point",
                    point=NativePoint(
                        frame="uia_physical_screen", x=bounds.x + bounds.width / 2, y=bounds.y + bounds.height / 2
                    ),
                ),
                limits=limits,
            )
            assert native_hit.nodes[0].reference == marker
            result = await env.computer.execute(ComputerClick(observation=screenshot.observation, point=point))
            assert result.effect == "executed"
            current = await state_until(
                session, state, lambda value, size=size: value["marker_clicks"] == (1 if size == 1280 else 2)
            )
        document = named["Native document"]
        bounds = document.bounds
        point_range = await a11y.range(
            document.reference,
            AccessibilityRangePoint(point=NativePoint(frame="uia_physical_screen", x=bounds.x + 10, y=bounds.y + 10)),
            limits=limits,
        )
        assert len(point_range.ranges) == 1
        assert (
            await a11y.range_read(
                point_range.ranges[0], AccessibilityRangeRead(kind="enclosing_element"), limits=limits
            )
        ).nodes

        await acknowledged(a11y, named["Unicode editor"].reference, {"op": "focus"})
        await state_until(session, state, lambda value: value["focused_editor"])
        assert (
            (await a11y.inspect(named["Unicode editor"].reference, limits=AccessibilityLimits(depth=0)))
            .nodes[0]
            .states["focused"]
        )
        await acknowledged(a11y, named["Alpha"].reference, {"op": "selection_select"})
        await state_until(session, state, lambda value: value["selection"] == ["Alpha"])
        await acknowledged(a11y, named["Beta"].reference, {"op": "selection_add"})
        await state_until(session, state, lambda value: set(value["selection"]) == {"Alpha", "Beta"})
        selected = await a11y.read(named["Options"].reference, AccessibilitySimpleRead(kind="selection"), limits=limits)
        assert {node.name for node in selected.nodes} == {"Alpha", "Beta"}
        await acknowledged(a11y, named["Alpha"].reference, {"op": "selection_remove"})
        await state_until(session, state, lambda value: value["selection"] == ["Beta"])
        selected = await a11y.read(named["Options"].reference, AccessibilitySimpleRead(kind="selection"), limits=limits)
        assert [node.name for node in selected.nodes] == ["Beta"]

        for value in ("", "Line 1\r\n中文😀\tLine 2"):
            await acknowledged(a11y, named["Unicode editor"].reference, {"op": "value_set", "value": value})
            await state_until(session, state, lambda observed, value=value: observed["text"] == value)
            content = await a11y.read(named["Unicode editor"].reference, AccessibilityTextRead(), limits=limits)
            assert content.text.text == value
            assert content.text.status == ("text" if value else "empty")
        await acknowledged(a11y, named["Numeric value"].reference, {"op": "range_value_set", "value": 42})
        await state_until(session, state, lambda value: value["numeric"] == 42)
        numeric = await a11y.read(
            named["Numeric value"].reference, AccessibilitySimpleRead(kind="numeric_value"), limits=limits
        )
        assert any(item.value.kind == "scalar" and item.value.value == 42 for item in numeric.properties)
        await acknowledged(a11y, named["Enable feature"].reference, {"op": "toggle"})
        await state_until(session, state, lambda value: value["checked"])
        toggle = await a11y.read(
            named["Enable feature"].reference, AccessibilityPropertyRead(groups=("value",)), limits=limits
        )
        assert any(
            "ToggleState" in item.name and item.value.kind == "scalar" and item.value.value == 1
            for item in toggle.properties
        )

        scroll = named["Fixture scroll area"].reference
        await acknowledged(a11y, scroll, {"op": "scroll_percent", "vertical": 100})
        await state_until(session, state, lambda value: value["scroll_offset"] == value["scrollable_height"] > 0)
        observed = await a11y.read(scroll, AccessibilitySimpleRead(kind="scroll"), limits=limits)
        assert any(
            "VerticalScrollPercent" in item.name and item.value.kind == "scalar" and item.value.value == 100
            for item in observed.properties
        )
        await acknowledged(a11y, scroll, {"op": "scroll_amount", "vertical": "large_decrement"})
        await state_until(session, state, lambda value: value["scroll_offset"] < value["scrollable_height"])
        await acknowledged(a11y, scroll, {"op": "scroll_percent", "vertical": 0})
        await state_until(session, state, lambda value: value["scroll_offset"] == 0)

        document_range = (
            await a11y.range(document.reference, AccessibilityRangeAcquire(kind="document"), limits=limits)
        ).ranges[0]
        absent = await a11y.range(
            document.reference, AccessibilityRangeFind(text="not in the fixture"), source=document_range, limits=limits
        )
        assert absent.completeness.complete and not absent.ranges
        moved = await a11y.range(
            document.reference,
            AccessibilityRangeMove(kind="move_endpoint", endpoint="end", unit="character", count=-1),
            source=document_range,
            limits=limits,
        )
        comparison = await a11y.range_read(
            document_range, AccessibilityRangeCompare(other=moved.ranges[0]), limits=limits
        )
        assert comparison.comparison is False
        assert (
            "Hello 世界😀"
            in (await a11y.range_read(document_range, AccessibilityRangeRead(kind="text"), limits=limits)).text.text
        )
        await acknowledged(
            a11y, document.reference, {"op": "range_scroll_into_view", "range": document_range.model_dump(mode="json")}
        )

        await acknowledged(a11y, root, {"op": "window_state_set", "state": "maximized"})
        await state_until(session, state, lambda value: value["window_state"] == "Maximized")
        await acknowledged(a11y, root, {"op": "window_state_set", "state": "normal"})
        await state_until(session, state, lambda value: value["window_state"] == "Normal")
        await acknowledged(a11y, root, {"op": "transform_move", "x": 100, "y": 100})
        moved_window = (await a11y.inspect(root, limits=AccessibilityLimits(depth=0))).nodes[0]
        assert moved_window.bounds.x == pytest.approx(100, abs=1)
        assert moved_window.bounds.y == pytest.approx(100, abs=1)
        await acknowledged(a11y, root, {"op": "transform_resize", "width": 1000, "height": 800})
        resized = (await a11y.inspect(root, limits=AccessibilityLimits(depth=0))).nodes[0]
        assert resized.bounds.width == pytest.approx(1000, abs=1)
        assert resized.bounds.height == pytest.approx(800, abs=1)
        await acknowledged(a11y, root, {"op": "window_close"})
        async with asyncio.timeout(5):
            while (await a11y.roots(AccessibilityRootScope(kind="application", process_id=pid), limits=limits)).nodes:
                await asyncio.sleep(0.05)
        with pytest.raises(EnvironmentError):
            await a11y.read(document.reference, AccessibilityTextRead(), limits=limits)


async def test_real_windows_dynamic_paging_unicode_and_budget_recovery():
    async with desktop(extended=True) as (session, state, pid), harness(session) as env:
        a11y = env.computer.accessibility
        root = await fixture_root(a11y, pid)
        limits = AccessibilityLimits(depth=12, max_nodes=24, max_native_calls=8192)
        nodes = await inspect_all(a11y, root, limits=limits)
        named = named_controls(nodes)
        unicode_name = "Straße Σς Hello 世界😀"
        for query, expected in (
            (AccessibilityFind(name=unicode_name), True),
            (AccessibilityFind(identifier=unicode_name), True),
            (AccessibilityFind(name="STRASSE \u03c3\u03c3", name_match="substring_casefold"), True),
            (AccessibilityFind(name="Stra"), False),
        ):
            result = await a11y.find(
                named[unicode_name].reference, query, limits=AccessibilityLimits(depth=0, max_text_bytes=4)
            )
            assert bool(result.nodes) == expected
            assert result.completeness.reason == "byte_limit"
        tree = named["Dynamic tree"].reference
        expected = {f"Dynamic item {index:03}" for index in range(280)}
        for view in ("raw", "control", "content"):
            found = await inspect_all(a11y, tree, limits=limits, view=view)
            assert {node.name for node in found if node.name and node.name.startswith("Dynamic item ")} == expected
        # A cooperative native-call limit must leave the retained cursor usable.
        page_limits = AccessibilityLimits(depth=1, max_nodes=2, max_native_calls=8192)
        first = await a11y.inspect(tree, limits=page_limits)
        cursor = first.completeness.continuation
        assert cursor is not None
        with pytest.raises(EnvironmentError) as exhausted:
            await a11y.inspect(tree, cursor=cursor, limits=page_limits.model_copy(update={"max_native_calls": 1}))
        assert exhausted.value.__cause__.error.data.error_type == "timeout"
        resumed = await a11y.inspect(tree, cursor=cursor, limits=page_limits)
        assert resumed.nodes
        recovered = [*first.nodes, *resumed.nodes]
        cursor = resumed.completeness.continuation
        for _ in range(128):
            if cursor is None:
                break
            page = await a11y.inspect(tree, cursor=cursor, limits=page_limits.model_copy(update={"max_nodes": 24}))
            recovered.extend(page.nodes)
            cursor = page.completeness.continuation
        else:
            pytest.fail("Recovered UIA traversal did not finish")
        handles = [node.reference.node_handle for node in recovered]
        assert len(handles) == len(set(handles))
        assert {node.name for node in recovered if node.name and node.name.startswith("Dynamic item ")} == expected
        # Retain the second sibling, then move it outside the inspected root.
        first = await a11y.inspect(tree, limits=page_limits)
        await acknowledged(a11y, named["Move dynamic anchor"].reference, {"op": "invoke"})
        moved = await state_until(session, state, lambda value: value["moved"])
        assert moved["move_count"] == 1 and moved["remove_count"] == 0
        assert moved["outside_count"] == 1 and moved["dynamic_count"] == 279
        with pytest.raises(EnvironmentError) as changed:
            await a11y.inspect(tree, cursor=first.completeness.continuation, limits=page_limits)
        assert changed.value.__cause__.error.data.error_type == "conflict"
        fresh = await inspect_all(a11y, tree, limits=limits)
        assert "Dynamic item 001" not in {node.name for node in fresh}
        assert "Outside tree" not in {node.name for node in fresh}
        # Removal is a distinct native change; fresh traversal must stay usable.
        await acknowledged(a11y, named["Remove dynamic anchor"].reference, {"op": "invoke"})
        removed = await state_until(session, state, lambda value: value["removed"])
        assert removed["move_count"] == 1 and removed["remove_count"] == 1
        assert removed["outside_count"] == 1 and removed["dynamic_count"] == 278
        fresh = await inspect_all(a11y, tree, limits=limits)
        assert {node.name for node in fresh if node.name and node.name.startswith("Dynamic item ")} == expected - {
            "Dynamic item 001",
            "Dynamic item 002",
        }


@pytest.mark.parametrize("mode", ["read", "readback"])
async def test_real_windows_blocked_provider_retires_helper_and_preserves_acknowledgement(mode):
    async with desktop(extended=True) as (session, state, pid), harness(session) as env:
        a11y = env.computer.accessibility
        limits = AccessibilityLimits(depth=12, max_native_calls=8192)
        root = await fixture_root(a11y, pid)
        found = await a11y.find(
            root, AccessibilityFind(name="Gated action"), limits=limits.model_copy(update={"max_matches": 1})
        )
        node = found.nodes[0].reference
        wire_node = eip.AccessibilityNodeReference(
            backend="uia", backend_epoch=node.backend_epoch, node_handle=node.node_handle
        )
        gate = state + ".gate"

        async def write_gate(suffix):
            async with session.open_writer(eip.EIPPath(path=gate + suffix), mode="create") as writer:
                await writer.write(b"true")
                await writer.commit()

        try:
            if mode == "read":
                await write_gate(".arm")
                with pytest.raises(EIPMethodError) as blocked:
                    await session.client.computer_a11y_inspect(
                        eip.ComputerA11yInspectParams(
                            context=eip.EIPCallContext(operation_id="blocked-read", timeout_ms=1000),
                            node=wire_node,
                            limits=eip.AccessibilityLimits(depth=0),
                        )
                    )
                assert blocked.value.error.data.error_type == "timeout"
            else:
                result = await session.client.computer_uia_invoke(
                    eip.ComputerUIAInvokeParams.model_validate(
                        {
                            "context": {"operation_id": "blocked-readback", "timeout_ms": 1000},
                            "node": wire_node.model_dump(),
                            "request": {"op": "invoke"},
                            "readback": True,
                        }
                    )
                )
                assert result.effect == "executed" and result.native_status == "returned_success"
                assert result.dispatch_complete and result.receipt.stage == "completed"
                assert result.application_outcome == "unverified" and result.observation is None
                # Independent fixture evidence, not an inference from acceptance.
                await state_until(session, gate + ".invoked", lambda value: value == 1)
            await state_until(session, gate + ".entered", lambda value: value is True)
        finally:
            await write_gate(".release")
        recovered = await a11y.describe()
        assert recovered.ready and recovered.backend_epoch != node.backend_epoch
        with pytest.raises(EnvironmentError) as stale:
            await a11y.inspect(node, limits=AccessibilityLimits(depth=0))
        assert stale.value.__cause__.error.data.error_type == "invalid_params"
        root = await fixture_root(a11y, pid)
        found = await a11y.find(
            root, AccessibilityFind(name="Gated action"), limits=limits.model_copy(update={"max_matches": 1})
        )
        assert found.nodes[0].reference.backend_epoch == recovered.backend_epoch
        assert (await a11y.inspect(found.nodes[0].reference, limits=AccessibilityLimits(depth=0))).nodes[
            0
        ].name == "Gated action"
