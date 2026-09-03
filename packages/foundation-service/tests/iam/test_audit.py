from __future__ import annotations

from datetime import datetime

from a13n_service.iam.audit import SystemAuditActor, security_audit_record
from a13n_service.iam.authorization import AuthenticatedActor
from a13n_service.iam.domain import PrincipalRef


def test_security_audit_record_maps_an_authenticated_actor_without_policy_changes() -> None:
    actor = AuthenticatedActor(
        principal=PrincipalRef(principal_type="service_account", principal_id="sa_1234567890abcdef"),
        auth_method="bearer",
        credential_id="pat_1234567890abcdef",
        boundary_workspace_id="ws_1234567890abcdef",
        request_id="req-audit-test",
    )
    occurred_at = datetime(2026, 9, 3, 10, 0)
    details: dict[str, object] = {"source_kind": "upload"}

    record = security_audit_record(
        audit_id="aud_1234567890abcdef",
        actor=actor,
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        action="asset.create",
        resource_type="asset",
        resource_id="asset_1234567890abcdef",
        outcome="failure",
        occurred_at=occurred_at,
        details=details,
    )

    assert record.id == "aud_1234567890abcdef"
    assert record.actor_type == "service_account"
    assert record.actor_id == "sa_1234567890abcdef"
    assert record.auth_method == "bearer"
    assert record.credential_id == "pat_1234567890abcdef"
    assert record.request_id == "req-audit-test"
    assert record.organization_id == "org_1234567890abcdef"
    assert record.workspace_id == "ws_1234567890abcdef"
    assert record.action == "asset.create"
    assert record.resource_type == "asset"
    assert record.resource_id == "asset_1234567890abcdef"
    assert record.outcome == "failure"
    assert record.occurred_at is occurred_at
    assert record.details is details


def test_security_audit_record_preserves_the_existing_system_actor_shape() -> None:
    record = security_audit_record(
        audit_id="audit_1234567890abcdef",
        actor=SystemAuditActor(request_id="op_1234567890abcdef"),
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        action="plugin_runtime.activate.succeeded",
        resource_type="plugin",
        resource_id="plg_1234567890abcdef",
        outcome="success",
        occurred_at=datetime(2026, 9, 3, 10, 0),
        details={"operation_id": "op_1234567890abcdef"},
    )

    assert record.id == "audit_1234567890abcdef"
    assert record.actor_type == "system"
    assert record.actor_id is None
    assert record.auth_method == "internal"
    assert record.credential_id is None
    assert record.request_id == "op_1234567890abcdef"
