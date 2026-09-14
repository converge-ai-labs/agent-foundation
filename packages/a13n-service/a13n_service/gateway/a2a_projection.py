"""A2A status and result values shared by current reads and committed events."""

import json

from a2a.types import a2a_pb2 as a2a
from google.protobuf.json_format import Parse, ParseDict
from google.protobuf.struct_pb2 import Value
from pydantic import JsonValue

from a13n_service.interactions.domain import RunStatus

TASK_STATE_BY_RUN_STATUS = {
    RunStatus.accepted.value: a2a.TASK_STATE_SUBMITTED,
    RunStatus.running.value: a2a.TASK_STATE_WORKING,
    RunStatus.completed.value: a2a.TASK_STATE_COMPLETED,
    RunStatus.failed.value: a2a.TASK_STATE_FAILED,
    RunStatus.cancelled.value: a2a.TASK_STATE_CANCELED,
}


def project_status(
    *,
    run_status: str,
    wait_reason: JsonValue,
    pending: JsonValue,
    failure: JsonValue,
    message_id: str,
) -> a2a.TaskStatus:
    if run_status == RunStatus.waiting.value:
        state = a2a.TASK_STATE_AUTH_REQUIRED if wait_reason == "authentication" else a2a.TASK_STATE_INPUT_REQUIRED
    else:
        state = TASK_STATE_BY_RUN_STATUS[run_status]
    status = a2a.TaskStatus(state=state)
    if run_status == RunStatus.waiting.value and isinstance(pending, dict):
        value = Value()
        ParseDict(pending, value)
        status.message.CopyFrom(a2a.Message(message_id=message_id, role=a2a.ROLE_AGENT, parts=[a2a.Part(data=value)]))
    elif run_status == RunStatus.failed.value:
        message = "The Agent task failed."
        if isinstance(failure, dict) and isinstance(candidate := failure.get("message"), str):
            message = candidate
        status.message.CopyFrom(a2a.Message(message_id=message_id, role=a2a.ROLE_AGENT, parts=[a2a.Part(text=message)]))
    return status


def project_artifacts(*, task_id: str, output_text: str | None, output: JsonValue) -> list[a2a.Artifact]:
    if output_text is not None:
        parts = [a2a.Part(text=output_text)]
    elif isinstance(output, str):
        parts = [a2a.Part(text=output)]
    elif output is not None:
        value = Value()
        Parse(json.dumps(output), value)
        parts = [a2a.Part(data=value)]
    else:
        return []
    return [a2a.Artifact(artifact_id=f"artifact-{task_id}-result", name="result", parts=parts)]
