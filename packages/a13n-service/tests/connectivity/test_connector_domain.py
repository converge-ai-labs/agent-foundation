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
        "connector_provider_id": "cnr_abcdef1234567890",
        "name": "GitHub",
        "connector_key": "github",
        "safe_metadata": {},
        "status": "pending",
        "status_reason": None,
        "version": 1,
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


@pytest.mark.parametrize(
    "legacy",
    [
        {"driver_key": "composio"},
        {"config_version": "v3_1"},
        {"endpoint": "https://other.example"},
        {"config": {}},
    ],
)
def test_provider_creation_rejects_removed_fields(legacy) -> None:
    from a13n_service.connectivity.connectors.domain import CreateConnectorProviderRequest

    with pytest.raises(ValidationError, match="Extra inputs"):
        CreateConnectorProviderRequest.model_validate(
            {
                "name": "Work",
                "type": "composio",
                "configuration": {"deployment": "cloud", "enabled_provider_slugs": ["github"]},
                "credentials": {"api_key": "secret"},
                **legacy,
            }
        )


def test_model_provider_rejects_old_configuration_field() -> None:
    from a13n_service.models.domain import CreateModelProviderRequest

    with pytest.raises(ValidationError, match="Extra inputs"):
        CreateModelProviderRequest.model_validate(
            {"name": "Work", "type": "openai", "config": {}, "credential": "secret"}
        )
