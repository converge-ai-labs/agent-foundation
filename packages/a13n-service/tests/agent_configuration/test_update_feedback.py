"""Safe diagnostics let a model correct a rejected update without exposing values."""

import json

import pytest
from a13n_service.agent_configuration.errors import update_failure_feedback
from a13n_service.agents.errors import agent_revision_create_failed
from a13n_service.application_errors import ApplicationError, ErrorCategory

from .test_editing import config, edit


def test_invalid_provider_field_is_actionable_and_never_echoes_value():
    with pytest.raises(ApplicationError) as caught:
        edit(
            config(),
            {"op": "set", "path": ["toolsets", "web", "config"], "value": {"web_provider_key": "private-secret"}},
        )
    feedback = update_failure_feedback(caught.value)
    assert "private-secret" not in feedback
    result = json.loads(feedback)
    assert result["code"] == "configuration_edit_invalid"
    assert result["issues"] == [{"path": ["toolsets", "web_provider_key"], "reason": "extra_forbidden"}]


@pytest.mark.parametrize(
    "code, expected",
    [
        ("configuration_metadata_invalid", "Omit creation_metadata"),
        ("configuration_version_conflict", "current version"),
        ("configuration_draft_terminal", "closed"),
    ],
)
def test_known_failures_give_specific_recovery_without_raw_messages(code, expected):
    feedback = update_failure_feedback(ApplicationError(code, "private-secret", category=ErrorCategory.conflict))
    assert expected in feedback
    assert "private-secret" not in feedback


def test_missing_provider_preserves_resolution_reason_and_field():
    result = json.loads(
        update_failure_feedback(
            agent_revision_create_failed("web_provider_required", path="toolsets.web.tools.search.config.provider_id")
        )
    )
    assert result["reason"] == "web_provider_required"
    assert result["path"] == ["toolsets", "web", "tools", "search", "config", "provider_id"]
    assert "same update" in result["hint"]


def test_unknown_failure_does_not_expose_resource_details():
    feedback = update_failure_feedback(
        ApplicationError(
            "internal_private",
            "private-secret",
            category=ErrorCategory.internal,
            details={"credential": "private-secret"},
        )
    )
    assert "private" not in feedback
    assert json.loads(feedback)["code"] == "configuration_update_unavailable"


def test_validation_diagnostics_are_bounded_and_sanitize_dynamic_paths():
    with pytest.raises(ApplicationError) as caught:
        edit(
            config(),
            {
                "op": "set",
                "path": [],
                "value": {
                    **config().model_dump(mode="json"),
                    **{f"https://user:private-secret@example.com/{i}": "private-secret" for i in range(12)},
                },
            },
        )
    feedback = update_failure_feedback(caught.value)
    assert "private-secret" not in feedback
    assert len(json.loads(feedback)["issues"]) == 8
