from __future__ import annotations

import math

import pytest
from a13n_harness.providers.environment.errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
)
from a13n_harness.providers.environment.models import EnvironmentProviderSpec, EnvironmentState
from pydantic import ValidationError


def test_provider_spec_detaches_finite_configuration_and_is_frozen() -> None:
    configuration = {"nested": {"values": [1, 2]}}
    spec = EnvironmentProviderSpec(
        provider_key="acme_sandbox",
        schema_version="1",
        configuration=configuration,
    )
    configuration["nested"]["values"].append(3)

    assert spec.configuration == {"nested": {"values": [1, 2]}}
    with pytest.raises(ValidationError):
        spec.provider_key = "other_provider"  # type: ignore[misc]


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, object()])
def test_provider_spec_rejects_non_json_configuration(value: object) -> None:
    with pytest.raises(ValidationError):
        EnvironmentProviderSpec(
            provider_key="acme_sandbox",
            schema_version="1",
            configuration={"value": value},
        )


def test_environment_state_detaches_provider_owned_json() -> None:
    payload = {"target": {"id": "sandbox-1"}}
    state = EnvironmentState(
        provider_key="acme_sandbox",
        state_version="1",
        state=payload,
    )
    payload["target"]["id"] = "sandbox-2"

    assert state.state == {"target": {"id": "sandbox-1"}}


def test_unknown_outcome_has_bounded_safe_projection() -> None:
    error = EnvironmentProviderError(
        "Vendor request timed out after dispatch request-id=private.",
        code="provider_unknown_outcome",
        category=EnvironmentProviderErrorCategory.UNKNOWN_OUTCOME,
        certainty=EnvironmentProviderOutcomeCertainty.UNKNOWN,
        recovery_hint=EnvironmentProviderRecoveryHint.RECONCILE,
        context=EnvironmentProviderErrorContext(
            provider_key="acme_sandbox",
            action="enter",
            operation_id="operation-1",
            resource_correlation="resource-1",
        ),
        details={"request_id": "private"},
    )

    safe = error.safe_projection()
    assert safe.message == "Environment provider outcome is unknown."
    assert "private" not in safe.model_dump_json()
    assert error.details == {"request_id": "private"}
    assert "private" in str(error)


def test_provider_error_rejects_malformed_codes() -> None:
    with pytest.raises(ValueError, match="stable identifiers"):
        EnvironmentProviderError(
            "bad code",
            code="Not Valid",
            category=EnvironmentProviderErrorCategory.PROVIDER_FAILURE,
            certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
        )
