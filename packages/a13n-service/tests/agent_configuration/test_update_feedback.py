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


@pytest.mark.parametrize("operations", [None, []])
def test_empty_update_rejected_at_model_boundary(operations):
    from a13n_service.agent_configuration.runtime import ModelDraftUpdate
    from pydantic import ValidationError

    payload = {"expected_version": 1, "content_digest": "a" * 64}
    if operations is not None:
        payload["operations"] = operations
    with pytest.raises(ValidationError, match="Provide configuration operations"):
        ModelDraftUpdate.model_validate(payload)
    payload["creation_metadata"] = {"name": "New agent"}
    assert ModelDraftUpdate.model_validate(payload).creation_metadata.name == "New agent"


@pytest.mark.parametrize("old_text", ["missing", "this"])
def test_text_match_errors_keep_actionable_reason(old_text):
    with pytest.raises(ApplicationError) as caught:
        edit(
            config(),
            {"op": "replace_text", "path": ["instructions"], "old_text": old_text, "new_text": "private-secret"},
        )
    result = json.loads(update_failure_feedback(caught.value))
    assert result["reason"] == "text_match_required"
    assert result["operation_index"] == 0
    assert "exactly one" in result["hint"]
    assert "private-secret" not in str(result)
