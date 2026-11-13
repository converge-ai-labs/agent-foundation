"""Public execution projections omit internal persisted ordering intent."""

import json

import pytest
from a13n_harness.tool_execution import ToolExecutionRecord
from a13n_service.runs.tables import ToolExecutionRow
from a13n_service.runs.tool_executions.views import ExecutionObservation, ExecutionPage, ExecutionView, view


@pytest.mark.parametrize("sequential", [False, True])
def test_public_execution_view_omits_persisted_ordering_intent(sequential):
    record = ToolExecutionRecord(
        execution_id="exec-original",
        thread_id="thread-original",
        run_id="harness-run",
        tool_call_id="call-original",
        tool_name="shell_exec",
        sequential=sequential,
    )
    persisted = record.model_dump(mode="json")
    row = ToolExecutionRow(
        id=record.execution_id,
        thread_id=record.thread_id,
        run_id="service-run",
        attempt_id="attempt-original",
        worker_id="worker-original",
        sequential=sequential,
        record=persisted,
        cancel_requested_at=None,
        first_delivered_at=None,
        expires_at=None,
    )

    projected = view(row)

    assert projected.run_id == "service-run"
    assert "sequential" not in projected.model_dump(mode="json")
    assert "sequential" not in json.loads(projected.model_dump_json())
    assert "sequential" not in ExecutionPage(items=[projected], next_cursor=None).model_dump(mode="json")["items"][0]
    assert persisted["sequential"] is sequential
    assert ToolExecutionRecord.model_validate(persisted).sequential is sequential


def test_public_execution_response_schema_omits_internal_ordering_intent():
    assert "sequential" not in ExecutionView.model_json_schema(mode="serialization")["properties"]
    for response in (ExecutionPage, ExecutionObservation):
        schema = response.model_json_schema(mode="serialization")
        assert "sequential" not in schema["$defs"]["ExecutionView"]["properties"]
    assert "sequential" in ToolExecutionRecord.model_json_schema(mode="serialization")["properties"]
