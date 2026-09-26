"""All first-party tool failures retain safe, actionable diagnostics."""

import json
from types import SimpleNamespace

import pytest
from a13n_harness.capabilities.working_state import CreateTask, TaskStateError
from a13n_harness.providers.environment.models import EnvironmentError
from a13n_harness.toolsets._results import validation_failure
from a13n_harness.toolsets.documents import DocumentsToolset
from a13n_harness.toolsets.files import _environment_error_result as file_failure
from a13n_harness.toolsets.shell import _environment_error_result as shell_failure
from a13n_harness.toolsets.web import WebConfiguration, WebToolset
from a13n_harness.toolsets.working_state import _task_error, _validate_note_key
from pydantic import ValidationError


def test_file_and_shell_failures_share_environment_projection():
    error = EnvironmentError(
        "private provider message",
        code="environment_request_invalid",
        retry_hint="request_change",
        details={"field": "cwd", "reason": "not_directory", "hint": "Choose a directory.", "private": "secret"},
    )
    file = file_failure(error)
    shell = shell_failure(error)
    assert {key: value for key, value in shell["error"].items() if key != "outcome_known"} == file["error"]
    assert file["error"] == error.safe_projection()
    assert "private" not in json.dumps(file)
    assert (
        shell_failure(EnvironmentError("private", code="environment_unknown_outcome"))["error"]["outcome_known"]
        is False
    )


def test_working_state_preserves_safe_explanation_and_validation_field():
    result = _task_error(TaskStateError("A task cannot depend on itself.", code="task_dependency_invalid"))
    assert result["error"]["message"] == "A task cannot depend on itself."
    assert result["error"]["retry_hint"] == "none"
    with pytest.raises(ValidationError) as exc:
        CreateTask(subject=" ", description="private validation input")
    invalid = validation_failure("task_request_invalid", exc.value)
    assert invalid["error"]["details"]["field"] == "subject"
    assert invalid["error"]["details"]["reason"] == "string_too_short"
    assert "private validation input" not in json.dumps(invalid)
    assert _validate_note_key("")["error"]["details"]["field"] == "key"


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["document", "download-directory", "download-file"])
async def test_environment_details_and_retry_are_not_lost_by_content_tools(operation):
    from a13n_harness.toolsets.web import WebResponse

    error = EnvironmentError(
        "private provider exception",
        code="environment_denied",
        retry_hint="new_run",
        details={
            "field": "path",
            "reason": "read_only",
            "hint": "Select an authorized writable destination.",
            "private": "private-value",
        },
    )

    class Files:
        async def stat(self, path):
            raise error

        async def mkdir(self, *args, **kwargs):
            if operation == "download-directory":
                raise error

        async def write_bytes_stream(self, *args, **kwargs):
            raise error

    async def body():
        yield b"payload"

    class Client:
        async def request(self, request, **kwargs):
            return WebResponse(
                status_code=200,
                final_url=request.url,
                canonical_url=request.url,
                headers={"content-type": "text/plain"},
                body=body(),
            )

    class Policy:
        async def authorize(self, *args, **kwargs):
            pass

    if operation == "document":
        result = await DocumentsToolset(SimpleNamespace(), files=Files()).pdf_convert(None, "/example.pdf")
    else:
        result = (
            await WebToolset(
                client=Client(), policy=Policy(), files=Files(), configuration=WebConfiguration()
            ).download(None, ["https://example.com/file.txt"], "/downloads")
        )[0]
    assert result["error"] == error.safe_projection()
    if operation == "download-file":
        assert result["url"] == "https://example.com/file.txt"
    assert "private" not in json.dumps(result)
