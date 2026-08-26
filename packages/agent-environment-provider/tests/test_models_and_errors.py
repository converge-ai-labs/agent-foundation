from __future__ import annotations

import math
from pathlib import Path
from typing import cast

import pytest
from converge_agent_environment_provider import (
    DirectLocalEnvironmentAttachment,
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
    EIPEnvironmentAttachment,
    EIPSessionSource,
    EnvironmentManagementAction,
    EnvironmentOperationContext,
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
    EnvironmentProviderSpec,
)
from pydantic import ValidationError


def test_provider_spec_detaches_finite_json_and_is_frozen() -> None:
    parameters = {"nested": {"values": [1, 2]}}
    spec = EnvironmentProviderSpec(
        provider_key="acme.sandbox",
        schema_version="1",
        parameters=parameters,
    )
    parameters["nested"]["values"].append(3)

    assert spec.parameters == {"nested": {"values": [1, 2]}}
    with pytest.raises(ValidationError):
        spec.provider_key = "other.provider"  # type: ignore[misc]


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, object()])
def test_provider_spec_rejects_non_json_parameters(value: object) -> None:
    with pytest.raises(ValidationError):
        EnvironmentProviderSpec(
            provider_key="acme.sandbox",
            schema_version="1",
            parameters={"value": value},
        )


def test_runtime_attachments_reject_malformed_public_values(tmp_path: Path) -> None:
    configuration = DirectLocalProviderConfiguration(
        environment_id="local-1",
        root=DirectLocalRootConfiguration(path=tmp_path),
    )
    with pytest.raises(ValueError):
        DirectLocalEnvironmentAttachment(
            attachment_id="",
            environment_id="local-1",
            configuration=configuration,
        )
    with pytest.raises(ValueError):
        DirectLocalEnvironmentAttachment(
            attachment_id="attachment-1",
            environment_id="local-other",
            configuration=configuration,
        )
    with pytest.raises(TypeError):
        EIPEnvironmentAttachment(
            attachment_id="attachment-1",
            environment_id="sandbox-1",
            session_source=cast(EIPSessionSource, object()),
        )


def test_unknown_outcome_requires_exact_reconciliation_context() -> None:
    operation = EnvironmentOperationContext(
        operation_id="operation-1",
        action=EnvironmentManagementAction.CREATE,
        resource_correlation="resource-1",
        attempt=1,
    )
    error = EnvironmentProviderError(
        "Vendor request timed out after dispatch request-id=private.",
        code="provider_unknown_outcome",
        category=EnvironmentProviderErrorCategory.UNKNOWN_OUTCOME,
        certainty=EnvironmentProviderOutcomeCertainty.UNKNOWN,
        recovery_hint=EnvironmentProviderRecoveryHint.RECONCILE,
        context=EnvironmentProviderErrorContext(
            provider_key="acme.sandbox",
            action=operation.action,
            operation_id=operation.operation_id,
            resource_correlation=operation.resource_correlation,
        ),
        details={"request_id": "private"},
    )

    safe = error.safe_projection()
    assert safe.message == "Environment provider outcome requires reconciliation."
    assert "private" not in safe.model_dump_json()
    assert error.details == {"request_id": "private"}
    assert "private" in str(error)


def test_third_party_error_codes_use_provider_key_namespace() -> None:
    with pytest.raises(ValueError, match="provider-key namespace"):
        EnvironmentProviderError(
            "bad namespace",
            code="other.failure",
            category=EnvironmentProviderErrorCategory.PROVIDER_FAILURE,
            certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
            context=EnvironmentProviderErrorContext(provider_key="acme.sandbox"),
        )
