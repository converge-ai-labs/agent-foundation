from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from a13n_harness.environment import EnvironmentMount
from a13n_harness.environment.accessibility import (
    AccessibilityCompleteness,
    AccessibilityLimits,
    AccessibilityNode,
    AccessibilityNodeReference,
    AccessibilityRangeReference,
    AccessibilityRootScope,
    AccessibilityTextContent,
    AccessibilityTree,
)
from a13n_harness.environment.accessibility_mutations import (
    AtspiMutation,
    AXMutation,
    UIAMutation,
    UIARequest,
)
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.environment.models import (
    ACCESSIBILITY_MUTATION_ACTIONS,
    ACCESSIBILITY_READ_ACTIONS,
    ENVIRONMENT_ACTION_DISPATCH,
    FILE_EXECUTION_ACTIONS,
    EnvironmentAction,
    EnvironmentError,
    EnvironmentPermissionSet,
)
from a13n_harness.environment.operations import EnvironmentOperations
from pydantic import TypeAdapter, ValidationError

from .test_environment_core import _Binding, _instance


def test_native_operation_unions_have_independent_catalog_actions():
    expected = set()
    for backend, union in (("ax", AXMutation), ("uia", UIAMutation), ("atspi", AtspiMutation)):
        mapping = TypeAdapter(union).json_schema()["discriminator"]["mapping"]
        expected.update(EnvironmentAction(f"environment.computer.{backend}_{op}") for op in mapping)
    assert expected == ACCESSIBILITY_MUTATION_ACTIONS
    assert len(expected) == 81
    assert not (expected | ACCESSIBILITY_READ_ACTIONS) & FILE_EXECUTION_ACTIONS
    for action in expected | ACCESSIBILITY_READ_ACTIONS:
        dispatch = ENVIRONMENT_ACTION_DISPATCH[action]
        assert dispatch.family == "computer"
        assert dispatch.facet == "accessibility"


@pytest.mark.parametrize("backend,unit", [("uia", "ax_native"), ("ax", "atspi_character"), ("atspi", "uia_opaque")])
def test_ranges_preserve_native_positioning(backend, unit):
    with pytest.raises(ValidationError, match="Range unit"):
        AccessibilityRangeReference(node=_node(backend=backend), range_handle="range-one", unit=unit)


@pytest.mark.parametrize("payload", ["a\0b", "😀" * 16385])
def test_mutations_bound_utf8_bytes_and_reject_nul(payload):
    with pytest.raises(ValidationError):
        UIARequest.model_validate({"request": {"op": "value_set", "value": payload}})


def test_closed_operations_reject_arbitrary_native_methods_and_arguments():
    with pytest.raises(ValidationError):
        UIARequest.model_validate({"request": {"op": "execute", "method": "SetValue"}})
    with pytest.raises(ValidationError):
        UIARequest.model_validate({"request": {"op": "invoke", "focus_first": True}})
    with pytest.raises(ValidationError):
        UIARequest.model_validate({"request": {"op": "range_value_set", "value": float("nan")}})


def test_text_status_distinguishes_empty_protected_and_missing():
    assert AccessibilityTextContent(status="empty", source="TextPattern", text="").text == ""
    assert AccessibilityTextContent(status="protected", source="IsPassword").text is None
    with pytest.raises(ValidationError):
        AccessibilityTextContent(status="protected", source="IsPassword", text="secret")
    with pytest.raises(ValidationError):
        AccessibilityCompleteness(complete=False)
    with pytest.raises(ValidationError):
        AccessibilityCompleteness(complete=True, reason="byte_limit")


def _node(*, execution_id="exec-one", generation="generation-one", backend="uia", epoch="epoch-one"):
    return AccessibilityNodeReference(
        execution_id=execution_id,
        observed_generation=generation,
        backend=backend,
        backend_epoch=epoch,
        node_handle="node-one",
    )


class _Accessibility:
    def __init__(self):
        self.binding = None
        self.calls = []
        self.result_reference = None

    def node(self):
        return _node(execution_id=self.binding.bound.execution_id, generation=self.binding.bound.descriptor.generation)

    async def roots(self, scope, *, limits):
        self.calls.append("roots")
        reference = self.result_reference or self.node()
        return AccessibilityTree(
            backend="uia",
            sampled_at=datetime.now(UTC),
            nodes=(AccessibilityNode(reference=reference, native_role="Button", sampled_at=datetime.now(UTC)),),
            completeness=AccessibilityCompleteness(complete=True),
        )

    async def inspect(self, node, **kwargs):
        self.calls.append("inspect")
        return await self.roots(AccessibilityRootScope(), limits=kwargs["limits"])

    async def describe(self):
        raise AssertionError("Unexpected describe")

    async def find(self, node, query, **kwargs):
        return await self.inspect(node, **kwargs)

    async def read(self, node, request, **kwargs):
        raise AssertionError("Unexpected read")

    async def range(self, node, query, **kwargs):
        raise AssertionError("Unexpected range")

    async def range_read(self, reference, request, **kwargs):
        raise AssertionError("Unexpected range read")

    async def execute(self, node, request, *, readback=False):
        self.calls.append(request.request.op)
        raise AssertionError("Denied mutation reached the provider")


def _mount(name, *, guard=None, actions=ACCESSIBILITY_READ_ACTIONS):
    provider = _Accessibility()
    binding = _Binding(
        name,
        families=frozenset({"computer"}),
        operations=EnvironmentOperations(accessibility=provider),
        permissions=frozenset(actions),
    )
    provider.binding = binding
    mount = EnvironmentMount(
        source=binding, computer_guard=guard, permission_ceiling=EnvironmentPermissionSet(operations=actions)
    )
    return mount, provider


@pytest.mark.anyio
async def test_reference_routes_to_original_execution_not_current_default():
    first, one = _mount("one")
    second, two = _mount("two")
    runtime = create_environment_runtime(mounts={"one": first, "two": second}, default_mount="two")
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        tree = await env.computer.accessibility.roots(
            AccessibilityRootScope(), limits=AccessibilityLimits(), alias="one"
        )
        await env.computer.accessibility.inspect(tree.nodes[0].reference, limits=AccessibilityLimits())
    assert one.calls == ["roots", "inspect", "roots"]
    assert two.calls == []


@pytest.mark.anyio
async def test_provider_artifacts_cannot_cross_execution_or_generation():
    mount, provider = _mount("one")
    provider.result_reference = _node()
    runtime = create_environment_runtime(mounts={"one": mount}, default_mount="one")
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        with pytest.raises(EnvironmentError) as error:
            await env.computer.accessibility.roots(AccessibilityRootScope(), limits=AccessibilityLimits())
    assert error.value.code == "environment_provider_failure"


@pytest.mark.anyio
async def test_reads_use_computer_admission_and_do_not_create_screenshot_observations():
    calls = []

    @asynccontextmanager
    async def guard(call):
        try:
            yield
        finally:
            calls.append(call)

    mount, _ = _mount("one", guard=guard)
    runtime = create_environment_runtime(mounts={"one": mount}, default_mount="one")
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        await env.computer.accessibility.roots(AccessibilityRootScope(), limits=AccessibilityLimits())
    assert len(calls) == 1
    assert calls[0].action == EnvironmentAction.COMPUTER_A11Y_ROOTS
    assert calls[0].observation is None
    assert isinstance(calls[0].result, AccessibilityTree)


@pytest.mark.anyio
async def test_read_permission_does_not_authorize_semantic_mutation():
    mount, provider = _mount("one")
    runtime = create_environment_runtime(mounts={"one": mount}, default_mount="one")
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        tree = await env.computer.accessibility.roots(AccessibilityRootScope(), limits=AccessibilityLimits())
        with pytest.raises(EnvironmentError) as error:
            await env.computer.accessibility.execute(tree.nodes[0].reference, UIARequest(request={"op": "invoke"}))
    assert error.value.code == "environment_denied"
    assert provider.calls == ["roots"]


@pytest.mark.anyio
async def test_range_and_cursor_handles_must_belong_to_the_exact_target_node():
    from a13n_harness.environment.accessibility import AccessibilityCursor

    action = EnvironmentAction.COMPUTER_UIA_RANGE_SELECT
    mount, provider = _mount("one", actions=ACCESSIBILITY_READ_ACTIONS | {action})
    runtime = create_environment_runtime(mounts={"one": mount}, default_mount="one")
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        tree = await env.computer.accessibility.roots(AccessibilityRootScope(), limits=AccessibilityLimits())
        node = tree.nodes[0].reference
        other = node.model_copy(update={"node_handle": "other-node"})
        native_range = AccessibilityRangeReference(node=other, range_handle="other-range", unit="uia_opaque")
        with pytest.raises(EnvironmentError, match="another node"):
            await env.computer.accessibility.execute(
                node, UIARequest(request={"op": "range_select", "range": native_range})
            )
        with pytest.raises(EnvironmentError, match="another node"):
            await env.computer.accessibility.inspect(
                node, limits=AccessibilityLimits(), cursor=AccessibilityCursor(root=other, cursor_handle="other-cursor")
            )
    assert provider.calls == ["roots"]


@pytest.mark.anyio
async def test_mutation_readback_requires_independent_inspection_permission():
    actions = {EnvironmentAction.COMPUTER_A11Y_ROOTS, EnvironmentAction.COMPUTER_UIA_INVOKE}
    mount, provider = _mount("one", actions=actions)
    runtime = create_environment_runtime(mounts={"one": mount}, default_mount="one")
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        tree = await env.computer.accessibility.roots(AccessibilityRootScope(), limits=AccessibilityLimits())
        with pytest.raises(EnvironmentError) as error:
            await env.computer.accessibility.execute(
                tree.nodes[0].reference, UIARequest(request={"op": "invoke"}), readback=True
            )
    assert error.value.code == "environment_denied"
    assert provider.calls == ["roots"]


@pytest.mark.anyio
async def test_result_bounds_are_enforced_before_publishing_observations():
    mount, _ = _mount("one")
    runtime = create_environment_runtime(mounts={"one": mount}, default_mount="one")
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        with pytest.raises(EnvironmentError) as error:
            await env.computer.accessibility.roots(
                AccessibilityRootScope(), limits=AccessibilityLimits(max_result_bytes=16)
            )
    assert error.value.code == "environment_provider_failure"


@pytest.mark.anyio
async def test_advertised_accessibility_action_requires_a_materialized_method():
    mount, _ = _mount("one")
    mount.source.bound.operations = EnvironmentOperations(computer=object())
    runtime = create_environment_runtime(mounts={"one": mount}, default_mount="one")
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        with pytest.raises(EnvironmentError) as error:
            await env.computer.accessibility.roots(AccessibilityRootScope(), limits=AccessibilityLimits())
    assert error.value.code == "environment_provider_failure"


@pytest.mark.anyio
async def test_find_enforces_requested_match_limit(monkeypatch):
    from a13n_harness.environment.accessibility import AccessibilityFind

    mount, provider = _mount("one")
    original = provider.find

    async def find(node, query, **kwargs):
        result = await original(node, query, **kwargs)
        return result.model_copy(update={"nodes": result.nodes * 2})

    monkeypatch.setattr(provider, "find", find)
    runtime = create_environment_runtime(mounts={"one": mount}, default_mount="one")
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        tree = await env.computer.accessibility.roots(AccessibilityRootScope(), limits=AccessibilityLimits())
        with pytest.raises(EnvironmentError, match="Matches exceed"):
            await env.computer.accessibility.find(
                tree.nodes[0].reference,
                AccessibilityFind(),
                limits=AccessibilityLimits(max_matches=1),
            )


@pytest.mark.anyio
@pytest.mark.parametrize("collection", ["properties", "nested_values", "document_selections", "attributes"])
async def test_content_enforces_each_requested_item_limit(monkeypatch, collection):
    from a13n_harness.environment.accessibility import AccessibilityContent, AccessibilitySimpleRead

    mount, provider = _mount("one")

    async def read(node, request, *, limits):
        item = {"name": "value", "source": "test", "value": {"kind": "scalar", "value": 1}}
        payload = {}
        if collection == "properties":
            payload["properties"] = [item, item]
        elif collection == "nested_values":
            payload["properties"] = [{**item, "value": {"kind": "scalars", "values": [1, 2]}}]
        elif collection == "attributes":
            payload["attributes"] = [{"name": "AXValue", "parameterized": False, "writable": True}] * 2
        else:
            payload["document_selections"] = [
                {
                    "start": node,
                    "start_offset": 0,
                    "end": node,
                    "end_offset": 1,
                    "start_is_active": False,
                }
            ] * 2
        return AccessibilityContent(
            node=node,
            sampled_at=datetime.now(UTC),
            completeness=AccessibilityCompleteness(complete=True),
            **payload,
        )

    monkeypatch.setattr(provider, "read", read)
    runtime = create_environment_runtime(mounts={"one": mount}, default_mount="one")
    async with runtime.bind(thread_id="thread-one", run_id="run-one", instance=_instance(), host_refs={}) as env:
        tree = await env.computer.accessibility.roots(AccessibilityRootScope(), limits=AccessibilityLimits())
        with pytest.raises(EnvironmentError, match="Items exceed"):
            await env.computer.accessibility.read(
                tree.nodes[0].reference,
                AccessibilitySimpleRead(kind="selection"),
                limits=AccessibilityLimits(max_items=1),
            )
