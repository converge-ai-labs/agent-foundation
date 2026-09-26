"""Historical handoff interpretation belongs to the state decoder."""

import pytest
from a13n_harness._handoff import _HandoffState
from pydantic import ValidationError


def test_historical_handoff_decodes_without_obsolete_runtime_fields():
    state = _HandoffState.model_validate_json(
        '{"operation_id":"handoff-old","summary":"Retained summary","files":["README.md"],'
        '"kind":"handoff","preserve_recent_user_turns":2,"target_tokens":2000}'
    )
    assert state.model_dump(mode="json") == {
        "operation_id": "handoff-old",
        "summary": "Retained summary",
        "files": ["README.md"],
    }


def test_historical_compaction_is_not_a_pending_handoff():
    state = _HandoffState.model_validate_json(
        '{"operation_id":"compaction-old","summary":"Already compacted","kind":"compaction"}'
    )
    assert state == _HandoffState()


@pytest.mark.parametrize("value", [{"operation_id": "compaction-new"}, {"kind": "unknown"}, {"files": ["a", "a"]}])
def test_handoff_decoder_retains_current_validation(value):
    with pytest.raises(ValidationError):
        _HandoffState.model_validate(value)
