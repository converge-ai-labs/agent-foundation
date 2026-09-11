import json

import pytest
from a13n_harness_ui.interactive.tool_rows import failure_reason


@pytest.mark.parametrize(
    "error,expected",
    [
        (
            {"code": "task_dependency_invalid", "message": "A task cannot depend on itself.", "details": {}},
            "A task cannot depend on itself.",
        ),
        (
            {
                "code": "invalid",
                "message": "Input is invalid.",
                "details": {"field": "root", "hint": "Select a directory.", "reason": "not_directory"},
            },
            "root: Select a directory.",
        ),
        (
            {"code": "invalid", "message": "Input is invalid.", "details": {"reason": "invalid_input"}},
            "Input is invalid.",
        ),
        ({"code": "legacy_error"}, "legacy_error"),
    ],
)
def test_failure_rows_preserve_actionable_diagnostics_and_legacy_codes(error, expected):
    assert failure_reason(json.dumps({"ok": False, "error": error}), "failed") == expected


def test_failure_row_bounds_combined_field_and_diagnostic():
    error = {"code": "invalid", "details": {"field": "x" * 500, "hint": "y" * 1000}}
    assert len(failure_reason(json.dumps({"error": error}), "failed")) <= 240
