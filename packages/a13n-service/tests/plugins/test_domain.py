from __future__ import annotations

import pytest
from a13n_harness import SafeFailure
from a13n_service.iam import ResourceRef
from a13n_service.plugins import PluginTaskReceipt
from pydantic import ValidationError


def test_plugin_task_receipt_accepts_terminal_shapes() -> None:
    plugin = ResourceRef(
        resource_type="plugin",
        resource_id="plg_1234567890abcdef",
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
    )

    succeeded = PluginTaskReceipt(
        operation_id="op_succeeded12345678",
        status="succeeded",
        result_refs=(plugin,),
    )
    failed = PluginTaskReceipt(
        operation_id="op_failed12345678901",
        status="failed",
        error=SafeFailure(code="plugin_runtime_incompatible", message="The candidate Runtime is incompatible."),
    )

    assert succeeded.result_refs == (plugin,)
    assert failed.error is not None


@pytest.mark.parametrize(
    ("status", "result_refs", "error"),
    (
        (
            "running",
            (
                ResourceRef(
                    resource_type="plugin",
                    resource_id="plg_1234567890abcdef",
                    organization_id="org_1234567890abcdef",
                    workspace_id="ws_1234567890abcdef",
                ),
            ),
            None,
        ),
        ("failed", (), None),
        ("succeeded", (), SafeFailure(code="unexpected", message="Unexpected failure.")),
    ),
)
def test_plugin_task_receipt_rejects_invalid_status_shape(
    status: str,
    result_refs: tuple[ResourceRef, ...],
    error: SafeFailure | None,
) -> None:
    with pytest.raises(ValidationError):
        PluginTaskReceipt.model_validate(
            {
                "operation_id": "op_invalid1234567890",
                "status": status,
                "result_refs": result_refs,
                "error": error,
            }
        )
