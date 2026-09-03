from datetime import UTC, datetime

import pytest
from a13n_service.connectivity.connectors.domain import ConnectorConnection
from a13n_service.iam.domain import PrincipalRef
from pydantic import ValidationError


def _connection(**changes: object) -> ConnectorConnection:
    values: dict[str, object] = {
        "id": "cconn_abcdef1234567890",
        "organization_id": "org_abcdef1234567890",
        "workspace_id": "ws_abcdef1234567890",
        "connector_id": "cnr_abcdef1234567890",
        "owner_principal_ref": None,
        "name": "GitHub",
        "provider_key": "github",
        "safe_metadata": {},
        "status": "pending",
        "status_reason": None,
        "version": 1,
        "catalog_digest": None,
        "created_by": PrincipalRef(principal_type="user", principal_id="usr_abcdef1234567890"),
        "created_at": datetime(2026, 9, 3, tzinfo=UTC),
        "updated_at": datetime(2026, 9, 3, tzinfo=UTC),
    }
    values.update(changes)
    return ConnectorConnection.model_validate(values)


def test_connector_connection_status_reason_is_discriminated() -> None:
    assert _connection(status="action_required", status_reason="incompatible").status_reason == "incompatible"
    with pytest.raises(ValidationError, match="status_reason"):
        _connection(status="ready", status_reason="incompatible")
    with pytest.raises(ValidationError, match="status_reason"):
        _connection(status="action_required", status_reason=None)


def test_connector_connection_public_shape_excludes_external_reference() -> None:
    schema = ConnectorConnection.model_json_schema()
    assert "external_ref" not in schema["properties"]
