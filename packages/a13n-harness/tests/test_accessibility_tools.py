from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from a13n_harness.environment.accessibility import (
    AccessibilityCapability,
    AccessibilityCompleteness,
    AccessibilityContent,
    AccessibilityDescription,
    AccessibilityLimits,
    AccessibilityNode,
    AccessibilityRangeReference,
    AccessibilityRangeResult,
)
from a13n_harness.environment.accessibility_mutations import AccessibilityMutationResult
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.environment.models import (
    ACCESSIBILITY_READ_ACTIONS,
    EnvironmentAction,
    EnvironmentError,
    EnvironmentOperationReceipt,
)
from a13n_harness.toolsets._accessibility_references import AccessibilityReferences
from a13n_harness.toolsets.accessibility import _AX, _UIA, _Atspi, _Range
from a13n_harness.toolsets.computer import ComputerToolset
from pydantic import ValidationError

from .test_accessibility_environment import _mount, _node
from .test_environment_core import _instance


def test_reference_projection_keeps_native_ids_out_of_tool_schema_and_results():
    refs = AccessibilityReferences()
    node = _node()
    reference = refs.remember(node)
    schema, validate = refs.arguments(_UIA, allowed_ops=frozenset({"invoke"}))
    assert schema["$defs"]["AccessibilityNodeReference"]["type"] == "string"
    assert "node_handle" not in json.dumps(schema)
    assert refs.encode({"reference": node}) == {"reference": reference}
    parsed = validate({"ref": reference, "request": {"op": "invoke"}})
    assert parsed.ref == node
    assert parsed.request.op == "invoke"
    assert refs.remember(node) == reference
    with pytest.raises(EnvironmentError):
        validate({"ref": node.model_dump(), "request": {"op": "invoke"}})


def test_permissions_filter_grouped_literal_variants_and_runtime_validation():
    refs = AccessibilityReferences()
    reference = refs.remember(_node())
    schema, validate = refs.arguments(_UIA, allowed_ops=frozenset({"invoke"}))
    assert schema["$defs"]["UIACommand"]["properties"]["op"].get("const") == "invoke"
    for denied in ("toggle", "window_close", "nonexistent"):
        with pytest.raises(ValidationError):
            validate({"ref": reference, "request": {"op": denied}})


@pytest.mark.parametrize(
    "model,operations",
    [(_UIA, {"value_set", "scroll_percent"}), (_AX, {"value_string_set"}), (_Atspi, {"text_insert"})],
)
def test_projected_schema_preserves_native_field_and_cross_field_validation(model, operations):
    refs = AccessibilityReferences()
    reference = refs.remember(_node())
    _, validate = refs.arguments(model, allowed_ops=frozenset(operations))
    for operation in operations:
        if operation == "scroll_percent":
            invalid = {"op": operation}
        elif operation == "text_insert":
            invalid = {"op": operation, "position": -1, "text": "x"}
        else:
            invalid = {"op": operation, "value": "x\0y"}
        with pytest.raises(ValidationError):
            validate({"ref": reference, "request": invalid})


def test_nested_range_references_are_kind_checked_and_never_restored_in_new_registry():
    refs = AccessibilityReferences()
    node = _node()
    node_ref = refs.remember(node)
    native_range = AccessibilityRangeReference(node=node, range_handle="range-one", unit="uia_opaque")
    range_ref = refs.remember(native_range)
    _, validate = refs.arguments(_Range)
    args = {
        "ref": node_ref,
        "source": range_ref,
        "query": {"kind": "endpoint_by_range", "endpoint": "start", "other": range_ref, "other_endpoint": "end"},
    }
    parsed = validate(args)
    assert parsed.source == native_range
    assert parsed.query.other == native_range
    with pytest.raises(EnvironmentError):
        validate({**args, "source": node_ref})
    _, new_validate = AccessibilityReferences().arguments(_Range)
    with pytest.raises(EnvironmentError):
        new_validate(args)


@pytest.mark.anyio
async def test_canonical_computer_names_match_actual_tools_and_exact_selection():
    from a13n_harness.environment.models import COMPUTER_ACTIONS

    mount, _ = _mount("one", actions=COMPUTER_ACTIONS)
    runtime = create_environment_runtime(mounts={"one": mount}, default_mount="one")
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        tools = ComputerToolset(env)
        assert ComputerToolset.available_names() == frozenset(tools.get_toolset().tools)
        assert len(ComputerToolset.available_names()) == 21
        for name in ComputerToolset.available_names():
            assert set(tools.get_toolset(allowed_names=frozenset({name})).tools) == {name}
        assert not tools.get_toolset(allowed_names=frozenset({"computer_a11y_roots"})).tools


@pytest.mark.anyio
async def test_tools_acquire_inspect_and_deny_hidden_operations_without_native_handle_input():
    mount, provider = _mount("one")
    runtime = create_environment_runtime(mounts={"one": mount}, default_mount="one")
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        toolset = ComputerToolset(env).get_toolset()
        assert len(toolset.tools) == 7
        assert "computer_uia" not in toolset.tools
        roots = await toolset.tools["computer_accessibility_roots"].function()
        assert roots["ok"]
        assert roots["alias"] == "one"
        assert roots["backend"] == "uia"
        assert roots["sampled_at"]
        assert roots["reported_at"]
        reference = roots["nodes"][0]["reference"]
        assert reference.startswith("node-")
        assert "execution_id" not in json.dumps(roots)
        inspected = await toolset.tools["computer_accessibility_inspect"].function(ref=reference)
        assert inspected["ok"]
        assert inspected["nodes"][0]["reference"] == reference
        invalid = await toolset.tools["computer_accessibility_inspect"].function(ref="node-unavailable")
        assert not invalid["ok"]
    assert provider.calls == ["roots", "inspect", "roots"]


@pytest.mark.anyio
async def test_backend_tool_schema_has_only_union_of_mount_permissions():
    actions = ACCESSIBILITY_READ_ACTIONS | {EnvironmentAction.COMPUTER_UIA_INVOKE}
    mount, _ = _mount("one", actions=actions)
    runtime = create_environment_runtime(mounts={"one": mount}, default_mount="one")
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        toolset = ComputerToolset(env).get_toolset()
        tool = toolset.tools["computer_uia"]
        schema = tool.function_schema.json_schema
        assert schema["$defs"]["UIACommand"]["properties"]["op"].get("const") == "invoke"
        roots = await toolset.tools["computer_accessibility_roots"].function()
        denied = await tool.function(ref=roots["nodes"][0]["reference"], request={"op": "toggle"})
        assert not denied["ok"]
        assert denied["error"]["code"] == "environment_request_invalid"


@pytest.mark.anyio
async def test_result_metadata_and_capabilities_remain_on_reference_mount(monkeypatch):
    first, one = _mount("one", actions=ACCESSIBILITY_READ_ACTIONS | {EnvironmentAction.COMPUTER_UIA_INVOKE})
    second, _ = _mount("two")
    original_roots = one.roots

    async def roots(scope, *, limits):
        result = await original_roots(scope, limits=limits)
        node = result.nodes[0].model_copy(
            update={
                "capabilities": (AccessibilityCapability(operation="invoke", supported="yes", source="InvokePattern"),)
            }
        )
        return result.model_copy(update={"nodes": (node,)})

    async def describe():
        return AccessibilityDescription(backend="uia", ready=True, reason="ready", desktop_identity="one")

    async def read(node, request, *, limits):
        return AccessibilityContent(
            node=node, sampled_at=datetime.now(UTC), completeness=AccessibilityCompleteness(complete=True)
        )

    async def range_result(node, *args, **kwargs):
        return AccessibilityRangeResult(
            node=node,
            sampled_at=datetime.now(UTC),
            completeness=AccessibilityCompleteness(complete=True),
            ranges=(AccessibilityRangeReference(node=node, range_handle="native-range", unit="uia_opaque"),),
        )

    async def range_read(reference, *args, **kwargs):
        return await range_result(reference.node)

    async def execute(node, request, *, readback):
        return AccessibilityMutationResult(
            receipt=EnvironmentOperationReceipt(
                execution_id=node.execution_id,
                observed_generation=node.observed_generation,
                operation_id="op-test",
                stage="completed",
                outcome="succeeded",
            ),
            effect="executed",
            native_status="returned_success",
            dispatch_complete=True,
            observation=AccessibilityNode(reference=node, native_role="Button", sampled_at=datetime.now(UTC))
            if readback
            else None,
        )

    for name, handler in (
        ("roots", roots),
        ("describe", describe),
        ("read", read),
        ("range", range_result),
        ("range_read", range_read),
        ("execute", execute),
    ):
        monkeypatch.setattr(one, name, handler)
    runtime = create_environment_runtime(mounts={"one": first, "two": second}, default_mount="two")
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        tools = ComputerToolset(env).get_toolset().tools
        described = await tools["computer_accessibility_describe"].function(alias="one")
        acquired = await tools["computer_accessibility_roots"].function(alias="one")
        ref = acquired["nodes"][0]["reference"]
        inspected = await tools["computer_accessibility_inspect"].function(ref=ref)
        found = await tools["computer_accessibility_find"].function(ref=ref, query={"role": "button"})
        content = await tools["computer_accessibility_read"].function(ref=ref, request={"kind": "selection"})
        ranges = await tools["computer_accessibility_range"].function(ref=ref, query={"kind": "document"})
        ranged = await tools["computer_accessibility_range_read"].function(
            range_ref=ranges["ranges"][0], request={"kind": "bounds"}
        )
        mutated = await tools["computer_uia"].function(ref=ref, request={"op": "invoke"}, readback=True)
        for result in (described, acquired, inspected, found, content, ranges, ranged, mutated):
            assert result["ok"], result
            assert result["alias"] == "one"
            assert result["backend"] == "uia"
            assert datetime.fromisoformat(result["reported_at"]).tzinfo is not None
        assert inspected["nodes"][0]["capabilities"][0]["permitted"]
        assert mutated["application_outcome"] == "unverified"


@pytest.mark.anyio
@pytest.mark.parametrize("invalid", ["readback", "receipt"])
async def test_post_dispatch_evidence_failure_requires_reconciliation(monkeypatch, invalid):
    mount, provider = _mount("one", actions=ACCESSIBILITY_READ_ACTIONS | {EnvironmentAction.COMPUTER_UIA_INVOKE})

    async def execute(node, request, *, readback):
        provider.calls.append(request.request.op)
        receipt = EnvironmentOperationReceipt(
            execution_id=node.execution_id if invalid != "receipt" else "another-execution",
            observed_generation=node.observed_generation,
            operation_id="op-dispatched",
            stage="completed",
            outcome="succeeded",
        )
        observation = AccessibilityNode(
            reference=node.model_copy(update={"node_handle": "wrong-node"}),
            native_role="Button",
            sampled_at=datetime.now(UTC),
        )
        return AccessibilityMutationResult(
            receipt=receipt,
            effect="executed",
            native_status="returned_success",
            dispatch_complete=True,
            observation=observation,
        )

    monkeypatch.setattr(provider, "execute", execute)
    runtime = create_environment_runtime(mounts={"one": mount}, default_mount="one")
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        tools = ComputerToolset(env).get_toolset().tools
        roots = await tools["computer_accessibility_roots"].function()
        result = await tools["computer_uia"].function(
            ref=roots["nodes"][0]["reference"], request={"op": "invoke"}, readback=True
        )
        assert not result["ok"]
        assert result["error"]["code"] == "environment_unknown_outcome"
        assert result["error"]["retry_hint"] == "reconcile_first"
        assert result["error"]["details"]["dispatch_stage"] == "unknown"
        # The common safe projection retains reconciliation metadata, while
        # detailed native receipts stay local to the Environment diagnostic.
        assert "receipt" not in result["error"]["details"]
    assert provider.calls == ["roots", "invoke"]


@pytest.mark.anyio
async def test_empty_roots_still_identify_selected_mount(monkeypatch):
    mount, provider = _mount("one")
    original = provider.roots

    async def empty(scope, *, limits):
        return (await original(scope, limits=limits)).model_copy(update={"nodes": ()})

    monkeypatch.setattr(provider, "roots", empty)
    runtime = create_environment_runtime(mounts={"one": mount})
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        result = await ComputerToolset(env).get_toolset().tools["computer_accessibility_roots"].function(alias="one")
        assert result["ok"]
        assert result["alias"] == "one"
        assert result["backend"] == "uia"
        assert result["nodes"] == []


@pytest.mark.anyio
async def test_screenshot_hit_test_and_projection_keep_original_mount_and_coordinate_space(monkeypatch):
    from a13n_harness.environment.accessibility import AccessibilityImageProjection, AccessibilityRootScope
    from a13n_harness.environment.computer import ComputerObservation, ComputerPoint

    first, one = _mount("one")
    second, two = _mount("two")
    calls = []

    async def hit_test(observation, point, *, limits):
        calls.append((observation, point))
        return await one.roots(AccessibilityRootScope(), limits=limits)

    async def inspect(node, *, limits, view, cursor, projection):
        result = await one.roots(AccessibilityRootScope(), limits=limits)
        item = result.nodes[0].model_copy(
            update={
                "image_projection": AccessibilityImageProjection(
                    source_observation_id=projection.observation_id,
                    status="projected",
                    left=10,
                    top=20,
                    right=30,
                    bottom=40,
                )
            }
        )
        return result.model_copy(update={"nodes": (item,)})

    monkeypatch.setattr(one, "roots_at_point", hit_test, raising=False)
    monkeypatch.setattr(one, "inspect", inspect)
    runtime = create_environment_runtime(mounts={"one": first, "two": second}, default_mount="two")
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        await env.computer.accessibility.roots(AccessibilityRootScope(), limits=AccessibilityLimits(), alias="one")
        computer = ComputerToolset(env, coordinate_space="normalized_1000")
        observation = ComputerObservation(
            execution_id=one.node().execution_id,
            observed_generation=one.node().observed_generation,
            observation_id="native-observation",
            target_id="display-one",
            width=800,
            height=400,
            mime_type="image/png",
            captured_at=datetime.now(UTC),
        )
        computer._observations["obs-one"] = observation
        computer._accessibility.register_observation(observation, "one")
        tools = computer.get_toolset().tools
        roots = await tools["computer_accessibility_roots"].function(
            scope={
                "kind": "screenshot_point",
                "observation_id": "obs-one",
                "point": {"x": 500, "y": 250},
            }
        )
        assert roots["ok"] and roots["alias"] == "one", roots
        assert calls == [(observation, ComputerPoint(x=400, y=100))]
        projected = await tools["computer_accessibility_inspect"].function(
            ref=roots["nodes"][0]["reference"],
            projection_observation_id="obs-one",
        )
        assert projected["ok"]
        assert projected["nodes"][0]["image_projection"]["source_observation_id"] == "obs-one"
        denied = await tools["computer_accessibility_roots"].function(
            alias="two",
            scope={
                "kind": "screenshot_point",
                "observation_id": "obs-one",
                "point": {"x": 500, "y": 250},
            },
        )
        assert not denied["ok"]
        missing = await tools["computer_accessibility_roots"].function(
            scope={
                "kind": "screenshot_point",
                "observation_id": "obs-missing",
                "point": {"x": 1, "y": 1},
            }
        )
        assert not missing["ok"]
    assert two.calls == []
    assert len(calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "op,clipboard",
    [
        ("text_copy", EnvironmentAction.COMPUTER_WRITE_CLIPBOARD),
        ("text_cut", EnvironmentAction.COMPUTER_WRITE_CLIPBOARD),
        ("text_paste", EnvironmentAction.COMPUTER_READ_CLIPBOARD),
    ],
)
@pytest.mark.parametrize("allow_clipboard", [False, True])
async def test_capability_permission_includes_required_clipboard_authority(monkeypatch, op, clipboard, allow_clipboard):
    action = EnvironmentAction(f"environment.computer.atspi_{op}")
    actions = ACCESSIBILITY_READ_ACTIONS | {action} | ({clipboard} if allow_clipboard else set())
    from a13n_harness.environment.operations import EnvironmentOperations

    from .test_computer_environment import Computer

    mount, provider = _mount("one", actions=actions)
    computer = Computer()
    computer.binding = provider.binding
    provider.binding.bound.operations = EnvironmentOperations(accessibility=provider, computer=computer)
    original = provider.roots

    async def roots(scope, *, limits):
        result = await original(scope, limits=limits)
        node = result.nodes[0]
        node = node.model_copy(
            update={
                "reference": node.reference.model_copy(update={"backend": "atspi"}),
                "capabilities": (AccessibilityCapability(operation=op, supported="candidate", source="atspi.Text"),),
            }
        )
        return result.model_copy(update={"backend": "atspi", "nodes": (node,)})

    monkeypatch.setattr(provider, "roots", roots)
    runtime = create_environment_runtime(mounts={"one": mount}, default_mount="one")
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        tools = ComputerToolset(env).get_toolset().tools
        result = await tools["computer_accessibility_roots"].function()
        assert result["ok"], result
        assert result["nodes"][0]["capabilities"][0]["permitted"] is allow_clipboard
