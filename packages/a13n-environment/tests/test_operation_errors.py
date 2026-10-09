"""Safe diagnostics survive provider adaptation without exposing native causes."""

import json

import pytest
from a13n_envd_client.eip import v1 as eip
from a13n_envd_client.errors import EIPMethodError
from a13n_environment.eip._common import convert_error
from a13n_environment.models import EnvironmentError


def test_operation_projection_keeps_only_bounded_safe_fields():
    error = EnvironmentError(
        "secret provider exception /private/host/path",
        code="environment_request_invalid",
        retry_hint="request_change",
        details={
            "field": "root",
            "reason": "not_directory",
            "hint": "Choose a directory.",
            "private": "secret",
            "operation_id": "private-operation",
            "timeout_seconds": 5,
            "edit_index": 1,
            "occurrences": 2,
            "missing": ["files"],
        },
    )
    projected = error.safe_projection()
    assert projected["code"] == error.code
    assert projected["message"] == "Environment operation input is invalid."
    assert projected["retry_hint"] == "request_change"
    assert projected["details"] == {
        "field": "root",
        "reason": "not_directory",
        "hint": "Choose a directory.",
        "timeout_seconds": 5,
        "edit_index": 1,
        "occurrences": 2,
        "missing": ["files"],
    }
    assert "secret" not in json.dumps(projected)
    assert "/private/" not in json.dumps(projected)


def test_legacy_operation_errors_still_have_a_safe_message_and_corrective_hint():
    for code in ("environment_request_invalid", "environment_denied", "environment_timeout", "custom_provider_error"):
        value = EnvironmentError("private cause", code=code).safe_projection()
        assert value["code"] == code
        assert value["message"]
        assert value["details"]["hint"]
        assert "private cause" not in json.dumps(value)


def test_projection_bounds_diagnostics_and_rejects_invalid_numeric_evidence():
    value = EnvironmentError(
        "private",
        code="environment_request_invalid",
        details={
            "field": "f" * 1000,
            "reason": "r" * 1000,
            "hint": "h" * 10000,
            "timeout_seconds": float("inf"),
            "occurrences": True,
            "edit_index": 10**1000,
            "missing": ["x" * 1000] * 100,
        },
    ).safe_projection()
    details = value["details"]
    assert len(details["field"]) == len(details["reason"]) == 128
    assert len(details["hint"]) == 1024
    assert len(details["missing"]) == 32 and len(details["missing"][0]) == 128
    assert not {"timeout_seconds", "occurrences", "edit_index"}.intersection(details)
    json.dumps(value, allow_nan=False)


def method_error(error_type, code, *, field=None, safe_detail=None, stage="pre_dispatch", retry="never"):
    return EIPMethodError(
        eip.EIPError(
            code=code,
            message="private daemon exception",
            data=eip.EIPErrorData(
                error_type=error_type,
                retry_hint=retry,
                dispatch_stage=stage,
                field=field,
                safe_detail=safe_detail,
                operation_id="private-operation",
                device_id="private-device",
                session_id="private-session",
                generation=7,
            ),
        )
    )


@pytest.mark.parametrize("field,reason", [("root", "not_searchable"), ("path", "not_file"), ("cwd", "not_directory")])
def test_eip_non_pattern_diagnostics_survive_adaptation(field, reason):
    error = convert_error(method_error(eip.ErrorType.INVALID_PARAMS, -32602, field=field, safe_detail=reason))
    value = error.safe_projection()
    assert value["details"]["field"] == field
    assert value["details"]["reason"] == reason
    assert value["details"]["hint"]
    assert value["details"]["dispatch_stage"] == "pre_dispatch"
    assert "private" not in json.dumps(value)


def test_eip_does_not_project_unrecognized_daemon_text_as_safe_details():
    error = convert_error(
        method_error(eip.ErrorType.INVALID_PARAMS, -32602, field="private-field", safe_detail="private daemon details")
    )
    assert "private" not in json.dumps(error.safe_projection())


def test_eip_unknown_outcome_is_not_downgraded_to_provider_failure():
    error = convert_error(method_error(eip.ErrorType.UNKNOWN_OUTCOME, -32042, stage="unknown", retry="reconcile_first"))
    assert error.code == "environment_unknown_outcome"
    assert error.retry_hint == "reconcile_first"
    assert error.safe_projection()["details"]["provider_retry_hint"] == "reconcile_first"
    assert "Reconcile" in error.safe_projection()["details"]["hint"]


@pytest.mark.parametrize("stage", ["pre_dispatch", "unknown"])
@pytest.mark.parametrize(
    "kind,reason",
    [
        (eip.ErrorType.PROVIDER_UNAVAILABLE, "computer_windows_desktop_unavailable"),
        (eip.ErrorType.CONFLICT, "computer_input_held"),
        (eip.ErrorType.UNSUPPORTED, "computer_scroll_steps_required"),
    ],
)
def test_computer_native_guidance_is_allowlisted_and_preserves_dispatch_evidence(stage, kind, reason):
    code = {
        eip.ErrorType.PROVIDER_UNAVAILABLE: -32050,
        eip.ErrorType.CONFLICT: -32060,
        eip.ErrorType.UNSUPPORTED: -32012,
    }[kind]
    value = convert_error(method_error(kind, code, safe_detail=reason, stage=stage)).safe_projection()
    assert value["details"]["reason"] == reason
    assert value["details"]["dispatch_stage"] == stage
    assert ("No input was dispatched" in value["details"]["hint"]) == (stage == "pre_dispatch")
    assert "private" not in json.dumps(value)
    wrong_type = convert_error(method_error(eip.ErrorType.DENIED, -32010, safe_detail=reason)).safe_projection()
    assert "reason" not in wrong_type["details"]
