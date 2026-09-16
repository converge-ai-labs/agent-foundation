from __future__ import annotations

import pytest
from a13n_service.agent_configuration.editing import UpdateDraftRequest, edit_config
from a13n_service.agents.domain import AgentConfig
from a13n_service.application_errors import ApplicationError
from pydantic import ValidationError


def config() -> AgentConfig:
    return AgentConfig.model_validate(
        {
            "model": {"model_key": "primary"},
            "input_adapter": {"adapter_key": "text"},
            "protocol": {"public_name": "Example"},
            "instructions": "Keep this policy. Change this sentence.",
            "plugins": [{"instance_name": "existing", "plugin_key": "example", "config": {"retained": True}}],
        }
    )


def edit(original: AgentConfig | None, *operations: dict) -> AgentConfig:
    request = UpdateDraftRequest.model_validate({"expected_version": 1, "operations": operations})
    return edit_config(original, request.operations)


def test_ordered_edits_preserve_unrelated_configuration_and_original() -> None:
    original = config()
    candidate = edit(
        original,
        {"op": "replace_text", "path": ["instructions"], "old_text": "Change this sentence.", "new_text": "Be brief."},
        {"op": "set", "path": ["model", "settings", "temperature"], "value": 0.2},
    )
    assert candidate.instructions == "Keep this policy. Be brief."
    assert candidate.model.settings == {"temperature": 0.2}
    assert candidate.plugins == original.plugins
    assert original.model.settings == {}
    assert original.instructions.endswith("Change this sentence.")


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "remove", "path": []},
        {"op": "remove", "path": ["model"]},
        {"op": "set", "path": ["owner"], "value": "another-user"},
        {"op": "set", "path": ["plugins", "0", "config"], "value": {}},
        {"op": "set", "path": ["plugins", "*"], "value": {}},
        {"op": "replace_text", "path": ["instructions"], "old_text": "missing", "new_text": "x"},
        {"op": "replace_text", "path": ["instructions"], "old_text": "this", "new_text": "x"},
        {"op": "replace_text", "path": ["model"], "old_text": "primary", "new_text": "x"},
    ],
)
def test_failed_command_leaves_all_original_fields_unchanged(operation: dict) -> None:
    original = config()
    before = original.model_dump(mode="json")
    with pytest.raises(ApplicationError):
        edit(original, {"op": "set", "path": ["model", "settings", "temperature"], "value": 0.5}, operation)
    assert original.model_dump(mode="json") == before


def test_empty_draft_requires_complete_initialization() -> None:
    with pytest.raises(ApplicationError):
        edit(None, {"op": "set", "path": ["instructions"], "value": "hello"})
    expected = config()
    assert edit(None, {"op": "set", "path": [], "value": expected.model_dump(mode="json")}) == expected


def test_root_replacement_cannot_silently_drop_existing_fields() -> None:
    original = config()
    replacement = original.model_dump(mode="json")
    del replacement["plugins"]
    with pytest.raises(ApplicationError):
        edit(original, {"op": "set", "path": [], "value": replacement})
    replacement["plugins"] = []
    assert edit(original, {"op": "set", "path": [], "value": replacement}).plugins == ()


def test_validation_error_does_not_return_submitted_values() -> None:
    with pytest.raises(ApplicationError) as raised:
        edit(config(), {"op": "set", "path": ["model", "credential"], "value": "private-secret"})
    assert "private-secret" not in raised.value.message
    assert "private-secret" not in str(raised.value.details)


@pytest.mark.parametrize("count", [0, 33])
def test_operation_count_is_bounded(count: int) -> None:
    with pytest.raises(ValidationError):
        UpdateDraftRequest.model_validate(
            {"expected_version": 1, "operations": [{"op": "set", "path": ["instructions"], "value": "x"}] * count}
        )


def test_empty_text_search_is_rejected() -> None:
    with pytest.raises(ValidationError):
        edit(config(), {"op": "replace_text", "path": ["instructions"], "old_text": "", "new_text": "x"})
