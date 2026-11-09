from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from a13n_envd_client.eip import v1 as eip
from a13n_envd_client.errors import EIPTransportClosedError
from a13n_harness.environment.accessibility import (
    AccessibilityLimits,
    AccessibilityNodeReference,
    AccessibilityRootScope,
    AccessibilityTree,
)
from a13n_harness.environment.accessibility_mutations import AccessibilityMutation
from a13n_harness.environment.models import ACCESSIBILITY_MUTATION_ACTIONS, ACCESSIBILITY_READ_ACTIONS, EnvironmentError
from a13n_harness.providers.environment.eip.accessibility import _MUTATIONS, EIPAccessibilityOperations
from a13n_harness.providers.environment.eip.binding import EIP_METHOD_ACTIONS
from pydantic import TypeAdapter

MUTATION = TypeAdapter(AccessibilityMutation)
SCHEMA = MUTATION.json_schema()
NOW = datetime.now(UTC)


def node(backend="atspi"):
    return AccessibilityNodeReference(
        execution_id="exec", observed_generation="gen", backend=backend, backend_epoch="epoch", node_handle="node"
    )


def example(schema, backend):
    """Build the required fields of one real public variant, including owning references."""
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if name == "AccessibilityNodeReference":
            return node(backend).model_dump(mode="json")
        if name == "AccessibilityRangeReference":
            return {
                "node": node(backend).model_dump(mode="json"),
                "range_handle": "range",
                "unit": {"atspi": "atspi_character", "ax": "ax_native", "uia": "uia_opaque"}[backend],
            }
        return example(SCHEMA["$defs"][name], backend)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "default" in schema:
        return schema["default"]
    if "anyOf" in schema:
        return example(schema["anyOf"][0], backend)
    match schema.get("type"):
        case "object":
            values = {key: example(schema["properties"][key], backend) for key in schema.get("required", ())}
            if schema.get("title") == "UIAScrollPercent":
                values["horizontal"] = 50
            return values
        case "array":
            return [example(schema["items"], backend) for _ in range(schema.get("minItems", 0))]
        case "string":
            return "x" * max(schema.get("minLength", 0), 1)
        case "integer" | "number":
            return max(schema.get("minimum", 0), schema.get("exclusiveMinimum", 0) + 1)
        case "boolean":
            return False
    raise AssertionError(schema)


def mutation_for(backend, op):
    wrapper = SCHEMA["discriminator"]["mapping"][backend].rsplit("/", 1)[-1]
    request_schema = SCHEMA["$defs"][wrapper]["properties"]["request"]
    while "$ref" in request_schema:
        request_schema = SCHEMA["$defs"][request_schema["$ref"].rsplit("/", 1)[-1]]
    request = example({"$ref": request_schema["discriminator"]["mapping"][op]}, backend)
    request["op"] = op
    return MUTATION.validate_python({"backend": backend, "request": request})


def session(requester):
    return SimpleNamespace(
        client=eip.EIPClient(requester),
        descriptor=SimpleNamespace(device_id="device", generation=1, session_id="session"),
    )


def requester(*, error=None):
    calls = []

    async def request(method, params):
        calls.append((method, params))
        if error is not None:
            raise error
        data = params.model_dump(mode="json", exclude_none=True)
        if method.name == "computer.a11y_roots":
            return eip.AccessibilityTree.model_validate(
                {
                    "backend": "atspi",
                    "sampled_at": NOW,
                    "completeness": {"complete": True},
                    "nodes": [
                        {
                            "reference": {"backend": "atspi", "backend_epoch": "epoch", "node_handle": "node"},
                            "native_role": "frame",
                            "sampled_at": NOW,
                        }
                    ],
                }
            )
        return eip.AccessibilityMutationResult.model_validate(
            {
                "receipt": {
                    "operation_id": data["context"]["operation_id"],
                    "method": method.name,
                    "device_id": "device",
                    "session_id": "session",
                    "generation": 1,
                    "request_digest": "a" * 64,
                    "stage": "completed",
                    "outcome": "succeeded",
                    "observed_at": NOW,
                },
                "effect": "executed",
                "native_status": "returned_success",
                "dispatch_complete": True,
            }
        )

    return SimpleNamespace(request=request, calls=calls)


def test_accessibility_protocol_inventory_is_exact():
    expected_reads = {action.value.removeprefix("environment.") for action in ACCESSIBILITY_READ_ACTIONS}
    expected_mutations = {action.value.removeprefix("environment.") for action in ACCESSIBILITY_MUTATION_ACTIONS}
    assert len(expected_reads) == 7
    assert len(expected_mutations) == 81
    assert set(_MUTATIONS) == expected_mutations
    for method in expected_reads | expected_mutations:
        spec = eip.METHODS[method]
        assert spec.introduced == "0.5"
        assert spec.replay_class == ("active_only" if method in expected_reads else "terminal_evidence")
        assert EIP_METHOD_ACTIONS[method] == (
            next(
                action
                for action in ACCESSIBILITY_READ_ACTIONS | ACCESSIBILITY_MUTATION_ACTIONS
                if action.value == "environment." + method
            ),
        )


@pytest.mark.anyio
@pytest.mark.parametrize("method", sorted(_MUTATIONS))
async def test_every_native_variant_uses_its_exact_generated_rpc(method):
    backend, op = method.removeprefix("computer.").split("_", 1)
    request = mutation_for(backend, op)
    transport = requester()
    adapter = EIPAccessibilityOperations(session(transport), execution_id="exec", generation="gen")
    result = await adapter.execute(node(backend), request)
    assert result.effect == "executed"
    assert result.application_outcome == "unverified"
    assert result.receipt.execution_id == "exec"
    assert len(transport.calls) == 1
    spec, params = transport.calls[0]
    assert spec.name == method
    data = params.model_dump(mode="json", exclude_none=True)
    assert data["request"]["op"] == op
    assert "execution_id" not in str(data)
    assert "observed_generation" not in str(data)


@pytest.mark.anyio
async def test_root_results_bind_execution_provenance():
    transport = requester()
    adapter = EIPAccessibilityOperations(session(transport), execution_id="exec", generation="gen")
    result = await adapter.roots(AccessibilityRootScope(), limits=AccessibilityLimits())
    assert result.nodes[0].reference == node()
    assert transport.calls[0][1].context.timeout_ms == 5000


@pytest.mark.parametrize("identity", ["exec", 'long-"\\\\\n😀' * 40])
@pytest.mark.parametrize("backend", ["ax", "uia", "atspi"])
def test_public_byte_reservation_covers_every_reference_occurrence(identity, backend):
    adapter = EIPAccessibilityOperations(session(requester()), execution_id=identity, generation=identity)
    reference = eip.AccessibilityNodeReference(backend=backend, backend_epoch="e", node_handle="n")
    native = eip.AccessibilityTree.model_validate(
        {
            "backend": backend,
            "sampled_at": NOW,
            "completeness": {
                "complete": False,
                "reason": "node_limit",
                "continuation": {"root": reference, "cursor_handle": "c"},
            },
            "nodes": [
                {
                    "reference": reference,
                    "parent": reference,
                    "children": [reference] * 50,
                    "native_role": "entry",
                    "sampled_at": NOW,
                    "capabilities": [
                        {
                            "operation": "do_action",
                            "supported": "yes",
                            "source": "native",
                            "action": {"node": reference, "action_handle": "a", "native_identifier": "click"},
                        }
                    ],
                }
            ],
        }
    )
    full_size = len(native.model_dump_json().encode("utf-8"))
    public = AccessibilityTree.model_validate(adapter._bind(native.model_dump(mode="json", exclude_none=True)))
    # Pick the smallest public budget whose adapted wire budget holds this full
    # result, including every nested parent, child, action and cursor reference.
    budget = (
        full_size * (adapter._reference_bytes + adapter._provenance_bytes) + adapter._reference_bytes - 1
    ) // adapter._reference_bytes
    limits = AccessibilityLimits(max_result_bytes=budget)
    wire = adapter._wire_limits(limits)
    assert full_size <= wire["max_result_bytes"] < budget
    assert len(public.model_dump_json().encode("utf-8")) <= budget
    assert "execution_id" not in wire


@pytest.mark.anyio
async def test_nested_foreign_reference_fails_before_wire_dispatch():
    transport = requester()
    adapter = EIPAccessibilityOperations(session(transport), execution_id="exec", generation="gen")
    request = mutation_for("atspi", "text_delete")
    foreign = request.request.range.node.model_copy(update={"execution_id": "other"})
    reference = request.request.range.model_copy(update={"node": foreign})
    request = request.model_copy(update={"request": request.request.model_copy(update={"range": reference})})
    with pytest.raises(EnvironmentError) as caught:
        await adapter.execute(node(), request)
    assert caught.value.details["dispatch_stage"] == "pre_dispatch"
    assert not transport.calls


@pytest.mark.anyio
async def test_transport_loss_after_mutation_is_never_replay_safe():
    transport = requester(error=EIPTransportClosedError("lost response"))
    adapter = EIPAccessibilityOperations(session(transport), execution_id="exec", generation="gen")
    with pytest.raises(EnvironmentError) as caught:
        await adapter.execute(node(), mutation_for("atspi", "focus"))
    assert caught.value.retry_hint == "reconcile_first"
    assert caught.value.details["dispatch_stage"] == "unknown"
    assert len(transport.calls) == 1


@pytest.mark.anyio
async def test_screenshot_hit_test_sends_only_session_observation_and_image_point():
    from a13n_harness.environment.computer import ComputerObservation, ComputerPoint

    transport = requester()
    operations = EIPAccessibilityOperations(session(transport), execution_id="exec", generation="gen")
    observation = ComputerObservation(
        execution_id="exec",
        observed_generation="gen",
        observation_id="obs-native",
        target_id="display-one",
        width=100,
        height=50,
        mime_type="image/png",
        captured_at=NOW,
    )
    await operations.roots_at_point(observation, ComputerPoint(x=10, y=20), limits=AccessibilityLimits())
    data = transport.calls[0][1].model_dump(mode="json", exclude_none=True)
    assert data["scope"] == {
        "kind": "screenshot_point",
        "observation_id": "obs-native",
        "image_point": {"x": 10, "y": 20},
    }
