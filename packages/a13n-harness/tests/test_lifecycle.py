import pytest
from a13n_harness.capabilities.lifecycle import _failed_payload
from a13n_harness.events import MAX_FAILURE_DETAIL_CHARS
from pydantic_ai.exceptions import ModelHTTPError, ModelSchemaTooComplexError

_SCHEMA_HINT = "Simplify the strict schemas."


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Schema is too complex.", f"Schema is too complex.\n\n{_SCHEMA_HINT}"),
        (f"Schema is too complex. {_SCHEMA_HINT}", f"Schema is too complex. {_SCHEMA_HINT}"),
        (None, _SCHEMA_HINT),
        (
            "x" * MAX_FAILURE_DETAIL_CHARS,
            "x" * (MAX_FAILURE_DETAIL_CHARS - len(_SCHEMA_HINT) - 3) + f"…\n\n{_SCHEMA_HINT}",
        ),
    ],
    ids=["separate", "already-present", "hint-only", "truncated-message"],
)
def test_model_schema_failure_includes_bounded_hint(message: str | None, expected: str) -> None:
    error = ModelHTTPError.for_category(
        ModelSchemaTooComplexError,
        status_code=400,
        model_name="test",
        body={"error": {"message": message}} if message else None,
        hint=_SCHEMA_HINT,
    )
    payload = _failed_payload(error, request_id="model-request-1", request_index=0)
    assert payload.status_code == 400
    assert payload.detail == expected


def test_model_http_failure_without_hint_keeps_provider_message() -> None:
    error = ModelHTTPError(status_code=503, model_name="test", body={"error": {"message": "Not available."}})
    payload = _failed_payload(error, request_id="model-request-1", request_index=0)
    assert payload.status_code == 503
    assert payload.detail == "Not available."
