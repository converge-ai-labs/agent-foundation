from __future__ import annotations

import json
import os
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from a13n_harness.environment.models import EnvironmentAction, EnvironmentPermissionSet
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.configuration import HarnessUiDocument, InputConfiguration
from a13n_harness_ui.root_input import RootInputFiles
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.surfaces import RootOperationStatus
from a13n_harness_ui.thread_files import AttachmentUpload, ComposerAttachment, ComposerInput, ThreadFiles
from anyio import Event, create_task_group, fail_after
from PIL import Image
from pydantic import ValidationError
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextContent, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_interactive import _seed


def _function_model(function):
    async def stream(messages, info):
        response = function(messages, info)
        if isinstance(response, Awaitable):
            response = await response
        for index, part in enumerate(response.parts):
            if isinstance(part, TextPart):
                yield part.content
            elif isinstance(part, ToolCallPart):
                yield {
                    index: DeltaToolCall(
                        name=part.tool_name, json_args=json.dumps(part.args_as_dict()), tool_call_id=part.tool_call_id
                    )
                }

    return FunctionModel(stream_function=stream)


def _text_parts(messages):
    return [
        item
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
        for item in ([part.content] if isinstance(part.content, str) else part.content)
    ]


def _references(messages):
    return [
        item
        for item in _text_parts(messages)
        if isinstance(item, TextContent) and (item.metadata or {}).get("harness_ui", {}).get("long_text")
    ]


async def _configuration(tmp_path, monkeypatch, *, threshold=8000, files_enabled=True, tools=None, projectless=False):
    path = await _seed(tmp_path, monkeypatch)
    document = yaml.safe_load(path.read_text())
    document["input"] = {"long_text_threshold_chars": threshold}
    document["tools"] = {"enable_codeact": False}
    if projectless:
        document["defaults"]["project"] = None
    path.write_text(yaml.safe_dump(document))
    agent_path = path.parent / "agents/codex.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    for capability in agent["capabilities"]:
        if capability["capability"] == "dynamic_environment":
            capability["configuration"]["files_enabled"] = files_enabled
    if tools is not None:
        agent["tools"] = tools
    agent_path.write_text(yaml.safe_dump(agent))
    return path


@asynccontextmanager
async def _app(tmp_path, monkeypatch, model, **options):
    path = await _configuration(tmp_path, monkeypatch, **options)
    monkeypatch.setattr("a13n_harness.model_auth.CodexRequestModel", lambda *args, **kwargs: model)
    settings = HarnessUiSettings(
        pricing_auto_update=False,
        storage=StorageSettings(data_root=tmp_path / "data", scratch_retention_seconds=1.0),
    )
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        yield app, path, settings


def test_long_text_configuration_defaults_and_disabled_value():
    assert HarnessUiDocument().input.long_text_threshold_chars == 8000
    assert HarnessUiDocument.model_validate({"input": {"long_text_threshold_chars": None}}).input == InputConfiguration(
        long_text_threshold_chars=None
    )
    for invalid in (0, -1, True, "8000", 1.5):
        with pytest.raises(ValidationError):
            InputConfiguration(long_text_threshold_chars=invalid)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "threshold,length,converted", [(8000, 8000, False), (8000, 8001, True), (None, 9000, False), (10, 11, True)]
)
async def test_root_input_threshold_has_no_inline_preview(tmp_path, monkeypatch, threshold, length, converted):
    observed = []

    def model(messages, info):
        observed.append(deepcopy(messages))
        return ModelResponse(parts=[TextPart("Received")])

    text = "界" * length
    async with _app(tmp_path, monkeypatch, _function_model(model), threshold=threshold) as (app, _, _):
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt=text)
        result = await app.wait_root_operation(receipt.receipt_id)
        assert result.status is RootOperationStatus.completed, result.failure
        references = _references(observed[0])
        assert bool(references) is converted
        if converted:
            reference = references[0]
            assert "界" not in reference.content
            assert "界" not in json.dumps(reference.metadata, ensure_ascii=False)
            metadata = reference.metadata["harness_ui"]
            attachment, data = await app.read_thread_attachment(
                thread_id=thread.thread_id, attachment_id=metadata["attachment"]["attachment_id"]
            )
            assert data == text.encode("utf-8")
            assert attachment.media_type == "text/plain"
            assert metadata["characters"] == length
            assert all(item != text for item in _text_parts(observed[0]))
        else:
            assert text in _text_parts(observed[0])


@pytest.mark.anyio
async def test_long_composer_text_preserves_order_metadata_and_survives_restart(tmp_path, monkeypatch):
    observed = []

    def model(messages, info):
        observed.append(deepcopy(messages))
        return ModelResponse(parts=[TextPart("Received")])

    text = "  request\r\n" + "保留这一行\r\n" * 1400 + "\n  "
    image = BytesIO()
    Image.new("RGB", (2, 2), "white").save(image, format="PNG")
    async with _app(tmp_path, monkeypatch, _function_model(model)) as (app, path, settings):
        thread = await app.create_thread()
        receipt = await app.submit_thread(
            thread_id=thread.thread_id,
            prompt=ComposerInput(
                (
                    text,
                    ComposerAttachment(AttachmentUpload("image.png", image.getvalue()), "image#1"),
                    ComposerAttachment(AttachmentUpload("notes.txt", b"notes"), "file#2"),
                ),
                source_id="input-long-text",
            ),
        )
        result = await app.wait_root_operation(receipt.receipt_id)
        assert result.status is RootOperationStatus.completed, result.failure
        reference = _references(observed[0])[0]
        assert reference.metadata["source_id"] == "input-long-text"
        assert any(isinstance(part, BinaryContent) for part in _text_parts(observed[0]))
        identifier = reference.metadata["harness_ui"]["attachment"]["attachment_id"]
        transcript = (await app.get_thread_transcript(thread_id=thread.thread_id)).model_dump_json()
        assert identifier in transcript and "input-long-text" in transcript
        assert text not in transcript
    scratch = tmp_path / "data/threads" / thread.thread_id / "tmp"
    os.utime(scratch, (1, 1))
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        assert not scratch.exists()
        assert (await app.read_thread_attachment(thread_id=thread.thread_id, attachment_id=identifier))[
            1
        ] == text.encode()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Recall the file")
        result = await app.wait_root_operation(receipt.receipt_id)
        assert result.status is RootOperationStatus.completed, result.failure
        assert _references(observed[-1])[0].metadata["harness_ui"]["attachment"]["attachment_id"] == identifier
        assert len(tuple((tmp_path / "data/threads" / thread.thread_id / "attachments").iterdir())) == 3
        assert all(not isinstance(part, TextContent) or part.content != text for part in _text_parts(observed[-1]))


@pytest.mark.anyio
@pytest.mark.parametrize("projectless", [False, True])
async def test_model_reads_long_input_using_real_environment_view(tmp_path, monkeypatch, projectless):
    calls = 0
    returned = []
    text = "Read this input.\n" + "line to inspect\n" * 600

    def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            reference = _references(messages)[0]
            file_path = reference.content.split("File: ", 1)[1].splitlines()[0]
            assert "view" in {tool.name for tool in info.function_tools}
            return ModelResponse(parts=[ToolCallPart("view", {"file_path": file_path, "line_limit": 2}, "read-input")])
        tool = messages[-1].parts[0]
        returned.append(tool.content)
        return ModelResponse(parts=[TextPart("Read from file")])

    async with _app(tmp_path, monkeypatch, _function_model(model), projectless=projectless) as (app, _, _):
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt=text)
        result = await app.wait_root_operation(receipt.receipt_id)
        assert result.status is RootOperationStatus.completed, result.failure
        assert calls == 2
        assert returned[0]["ok"], returned[0]
        assert returned[0]["content"].startswith("Read this input.\nline to inspect\n")


@pytest.mark.anyio
@pytest.mark.parametrize("files_enabled,tools", [(False, None), (True, ["shell_exec"])])
async def test_no_file_reader_keeps_original_input_and_reports_skip(tmp_path, monkeypatch, files_enabled, tools):
    observed = []

    def model(messages, info):
        observed.append(deepcopy(messages))
        return ModelResponse(parts=[TextPart("Received")])

    text = "x" * 8001
    async with _app(tmp_path, monkeypatch, _function_model(model), files_enabled=files_enabled, tools=tools) as (
        app,
        _,
        _,
    ):
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt=text)
        result = await app.wait_root_operation(receipt.receipt_id)
        assert result.status is RootOperationStatus.completed, result.failure
        assert text in _text_parts(observed[0])
        assert not _references(observed[0])
        assert any(isinstance(part, TextContent) and "kept inline" in part.content for part in _text_parts(observed[0]))
        assert not (tmp_path / "data/threads" / thread.thread_id / "attachments").exists()


@pytest.mark.anyio
async def test_long_input_write_failure_never_reaches_model(tmp_path, monkeypatch):
    calls = []

    def model(messages, info):
        calls.append(messages)
        return ModelResponse(parts=[TextPart("Unexpected")])

    async def fail_stage(self, thread_id, upload):
        raise OSError("input storage unavailable")

    async with _app(tmp_path, monkeypatch, _function_model(model)) as (app, _, _):
        monkeypatch.setattr(ThreadFiles, "stage", fail_stage)
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="x" * 8001)
        result = await app.wait_root_operation(receipt.receipt_id)
        assert result.status is RootOperationStatus.failed
        assert not calls


@pytest.mark.anyio
async def test_long_steering_uses_same_policy_and_stale_receipt_cannot_write(tmp_path, monkeypatch):
    started, release = Event(), Event()
    observed = []

    async def model(messages, info):
        observed.append(deepcopy(messages))
        if len(observed) == 1:
            started.set()
            await release.wait()
        return ModelResponse(parts=[TextPart("Received")])

    async with _app(tmp_path, monkeypatch, _function_model(model)) as (app, _, _):
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Wait for more input")
        with fail_after(10):
            await started.wait()
            text = "steered text\n" * 700
            control = await app.steer_root_operation(receipt_id=receipt.receipt_id, message=text)
            assert control.accepted
            release.set()
            result = await app.wait_root_operation(receipt.receipt_id)
        assert result.status is RootOperationStatus.completed, result.failure
        reference = _references(observed[-1])[0]
        assert "steered text" not in reference.content
        identifier = reference.metadata["harness_ui"]["attachment"]["attachment_id"]
        assert (await app.read_thread_attachment(thread_id=thread.thread_id, attachment_id=identifier))[
            1
        ] == text.encode()
        retained = tmp_path / "data/threads" / thread.thread_id / "attachments"
        before = set(retained.iterdir())
        assert not (await app.steer_root_operation(receipt_id=receipt.receipt_id, message=text)).accepted
        assert set(retained.iterdir()) == before


@pytest.mark.anyio
@pytest.mark.parametrize("missing_mount", [True, False])
async def test_missing_mount_or_text_permission_never_creates_reference(tmp_path, missing_mount):
    files = ThreadFiles(tmp_path)
    environment = Mock(spec=BoundEnvironment)
    mount = SimpleNamespace(
        name="thread-files",
        permission_ceiling=EnvironmentPermissionSet(
            operations=frozenset({EnvironmentAction.FILE_STAT, EnvironmentAction.FILE_READ_BYTES})
        ),
    )
    environment.snapshot = SimpleNamespace(mounts=() if missing_mount else (mount,))
    inputs = RootInputFiles(files, "thread-test", InputConfiguration(), view_enabled=True)
    text = "x" * 8001
    assert (await inputs.prepare(text, environment))[0] == text
    environment.resolve_files.assert_not_called()
    assert not files.root.exists()
    await files.close()


@pytest.mark.anyio
async def test_hidden_text_and_existing_references_are_not_converted(tmp_path):
    files = ThreadFiles(tmp_path)
    environment = Mock(spec=BoundEnvironment)
    inputs = RootInputFiles(files, "thread-test", InputConfiguration(long_text_threshold_chars=10), view_enabled=True)
    hidden = TextContent("hidden " * 20, metadata={"display": False})
    attachment = TextContent("reference " * 20, metadata={"harness_ui": {"attachment": {"attachment_id": "input-one"}}})
    parts = ("short", hidden, attachment)
    assert await inputs.prepare(parts, environment) == parts
    environment.resolve_files.assert_not_called()
    assert not files.root.exists()
    await files.close()


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["count", "file_bytes", "total_bytes"])
async def test_generated_input_files_obey_attachment_limits_before_writing(tmp_path, monkeypatch, kind):
    observed = []

    def model(messages, info):
        observed.append(messages)
        return ModelResponse(parts=[TextPart("Unexpected")])

    if kind == "count":
        prompt = tuple("x" * 8001 for _ in range(9))
    elif kind == "file_bytes":
        prompt = "x" * (10 * 1024 * 1024 + 1)
    else:
        prompt = tuple("x" * (7 * 1024 * 1024) for _ in range(3))
    async with _app(tmp_path, monkeypatch, _function_model(model)) as (app, _, _):
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt=prompt)
        result = await app.wait_root_operation(receipt.receipt_id)
        assert result.status is RootOperationStatus.failed
        assert not observed
        assert not (tmp_path / "data/threads" / thread.thread_id / "attachments").exists()


@pytest.mark.anyio
async def test_slow_steering_file_save_does_not_retarget_next_run(tmp_path, monkeypatch):
    started, release_model = Event(), Event()
    saving, release_save = Event(), Event()
    observed, controls = [], []

    async def model(messages, info):
        observed.append(deepcopy(messages))
        if len(observed) == 1:
            started.set()
            await release_model.wait()
        return ModelResponse(parts=[TextPart("Received")])

    original = ThreadFiles.stage

    async def slow_stage(self, thread_id, upload):
        saving.set()
        await release_save.wait()
        return await original(self, thread_id, upload)

    async with _app(tmp_path, monkeypatch, _function_model(model)) as (app, _, _):
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="First input")
        with fail_after(10):
            await started.wait()
            monkeypatch.setattr(ThreadFiles, "stage", slow_stage)

            async def steer():
                controls.append(await app.steer_root_operation(receipt_id=receipt.receipt_id, message="x" * 8001))

            async with create_task_group() as group:
                group.start_soon(steer)
                await saving.wait()
                release_model.set()
                assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
                next_receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Second input")
                release_save.set()
            assert not controls[0].accepted
            assert (await app.wait_root_operation(next_receipt.receipt_id)).status is RootOperationStatus.completed
        assert not _references(observed[-1])
        assert "Second input" in _text_parts(observed[-1])
