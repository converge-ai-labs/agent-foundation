"""Current Task reads and delayed push share values without sharing authority."""

from datetime import UTC, datetime

import pytest
from a2a.types import a2a_pb2 as a2a
from a13n_service.gateway.a2a import _task_artifacts, _task_status
from a13n_service.gateway.a2a_push import _event_artifacts, _event_status
from a13n_service.gateway.models import A2ATaskBindingRecord
from a13n_service.interactions.models import RunRecord
from a13n_service.lifecycle.models import LifecycleEventRecord


@pytest.mark.parametrize(
    ("status", "wait_reason", "expected"),
    [
        ("running", None, a2a.TASK_STATE_WORKING),
        ("waiting", "authentication", a2a.TASK_STATE_AUTH_REQUIRED),
        ("waiting", "approval", a2a.TASK_STATE_INPUT_REQUIRED),
        ("completed", None, a2a.TASK_STATE_COMPLETED),
        ("failed", None, a2a.TASK_STATE_FAILED),
        ("cancelled", None, a2a.TASK_STATE_CANCELED),
    ],
)
def test_read_and_push_status_preserve_source_identity(status, wait_reason, expected):
    run = RunRecord(
        id="run_current",
        status=status,
        wait_reason=wait_reason,
        pending_json={"requests": []},
        failure_json={"message": "The accepted operation failed."},
        updated_at=datetime(2026, 9, 9, tzinfo=UTC),
    )
    event = LifecycleEventRecord(
        id="lev_committed",
        event_type=f"run.{status}",
        payload={"wait_reason": wait_reason, "pending": run.pending_json, "failure": run.failure_json},
    )
    current, pushed = _task_status(run), _event_status(event)
    assert current.state == pushed.state == expected
    assert current.HasField("timestamp")
    assert not pushed.HasField("timestamp")
    if status in {"waiting", "failed"}:
        assert current.message.message_id == "status-run_current"
        assert pushed.message.message_id == "status-lev_committed"
        assert current.message.parts == pushed.message.parts


@pytest.mark.parametrize(
    ("text", "output"),
    [
        ("preferred", {"value": 1}),
        (None, "plain"),
        (None, {"value": [1, True]}),
        (None, [1, True]),
        (None, 2.5),
        (None, False),
        (None, None),
    ],
)
def test_delayed_result_projection_keeps_committed_output(text, output):
    task = A2ATaskBindingRecord(id="task_result")
    run = RunRecord(status="completed", output_text=text, output_json=output)
    event = LifecycleEventRecord(event_type="run.completed", payload={"output_text": text, "output": output})
    expected = _task_artifacts(task, run)
    assert _event_artifacts(task, event) == expected

    run.output_text = "a newer result"
    assert _task_artifacts(task, run) != expected
    assert _event_artifacts(task, event) == expected
