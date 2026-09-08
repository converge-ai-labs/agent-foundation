from __future__ import annotations

import json
import os
from pathlib import Path

from a13n_harness_ui.diagnostics import ISSUE_URL, exception_feedback


def test_report_retains_exception_chain_but_not_locals_or_source(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    local_secret = "local-secret-not-an-exception"
    try:
        try:
            raise ValueError("private provider detail")
        except ValueError as cause:
            raise RuntimeError("outer failure") from cause
    except RuntimeError as error:
        feedback = exception_feedback(error, thread_id="thread-test", run_id="run-test", phase="root_execution")

    report = next(tmp_path.glob("a13n-harness-ui-error-*.json"))
    data = json.loads(report.read_text())
    assert data["session_id"] == "thread-test"
    assert data["run_id"] == "run-test"
    assert "pydantic-ai" in data["releases"]
    assert len(data["exceptions"]) == 2
    assert "private provider detail" in report.read_text()
    assert local_secret not in report.read_text()
    assert "private provider detail" not in feedback
    assert all(set(frame) == {"file", "line", "function"} for item in data["exceptions"] for frame in item["frames"])
    if os.name != "nt":
        assert report.stat().st_mode & 0o777 == 0o600
    assert str(report) in feedback
    assert ISSUE_URL in feedback
    assert "Nothing was uploaded" in feedback


def test_report_captures_cleanup_causes_and_notes_without_syntax_source(tmp_path: Path, monkeypatch) -> None:
    from a13n_harness import RunCleanupError

    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    cause = SyntaxError("invalid syntax", ("fixture.py", 1, 1, "private_source_line"))
    error = RunCleanupError("cleanup failed", outcome=None, causes=(cause,))
    error.add_note("checkpoint selection failed")
    exception_feedback(error, thread_id=None, phase="test")
    content = next(tmp_path.glob("a13n-harness-ui-error-*.json")).read_text()
    assert "SyntaxError" in content
    assert "checkpoint selection failed" in content
    assert "private_source_line" not in content


def test_report_write_failure_still_provides_feedback(monkeypatch) -> None:
    def fail_write(**kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr("tempfile.mkstemp", fail_write)
    feedback = exception_feedback(RuntimeError("original failure"), thread_id=None, phase="test")
    assert "could not be saved" in feedback
    assert ISSUE_URL in feedback
    assert "disk unavailable" not in feedback


def test_report_respects_suppressed_private_causes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    try:
        try:
            raise ValueError("suppressed credential data")
        except ValueError:
            raise RuntimeError("safe boundary failure") from None
    except RuntimeError as error:
        exception_feedback(error, thread_id=None, phase="test")
    content = next(tmp_path.glob("a13n-harness-ui-error-*.json")).read_text()
    assert "safe boundary failure" in content
    assert "suppressed credential data" not in content
