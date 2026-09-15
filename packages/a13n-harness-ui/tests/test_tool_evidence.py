from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_harness import HarnessEvent
from a13n_harness.toolsets.events import FileEditAppliedEvent
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.thread_projection import _request_parts, _response_part
from a13n_harness_ui.tool_evidence import ToolEvidenceCollector, applied_edit
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ModelMessagesTypeAdapter,
    ModelRequest,
    NativeToolCallPart,
    NativeToolReturnPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio


def envelope(event, *, run_id="run-one"):
    return HarnessEvent(thread_id="thread-one", run_id=run_id, sequence=1, occurred_at=datetime.now(UTC), event=event)


def edit_event(call_id="call-one", *, before="before\n", after="after\n"):
    return FileEditAppliedEvent(tool_call_id=call_id, file_path="/work/a", before=before, after=after)


def test_applied_evidence_round_trips_without_changing_model_facing_result_or_other_metadata() -> None:
    collector = ToolEvidenceCollector(run_id="run-one")
    collector.observe(envelope(edit_event()))
    part = ToolReturnPart("edit", {"ok": False}, "call-one", metadata={"keep": "value"}, outcome="failed")
    collector.observe(envelope(FunctionToolResultEvent(part)))
    restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json([ModelRequest(parts=[part])]))
    saved = restored[0].parts[0]
    assert isinstance(saved, ToolReturnPart)
    assert saved.content == {"ok": False} and saved.metadata["keep"] == "value"
    projected = _request_parts(saved)[0]
    assert projected.applied_edit.before == "before\n"
    assert projected.applied_edit.after == "after\n"
    assert projected.outcome == "failed"
    assert applied_edit(ToolReturnPart("edit", {}, "old-history")) is None


def test_edit_retention_is_bounded_and_does_not_associate_other_runs_or_call_ids() -> None:
    collector = ToolEvidenceCollector(run_id="run-one")
    collector.observe(envelope(edit_event(), run_id="run-other"))
    unrelated = ToolReturnPart("edit", {}, "call-one")
    collector.observe(envelope(FunctionToolResultEvent(unrelated)))
    assert unrelated.metadata is None
    collector.observe(envelope(edit_event("inner")))
    outer = ToolReturnPart("call", {}, "outer")
    collector.observe(envelope(FunctionToolResultEvent(outer)))
    assert outer.metadata is None
    for index in range(10):
        call_id = f"call-{index}"
        collector.observe(envelope(edit_event(call_id, before="x" * 32768, after="y" * 32768)))
        part = ToolReturnPart("edit", {}, call_id)
        collector.observe(envelope(FunctionToolResultEvent(part)))
        evidence = applied_edit(part)
        assert evidence is not None
        assert evidence.omitted is (index >= 7)  # the earlier unmatched event also consumed retained space
        if evidence.omitted:
            assert evidence.before is None and evidence.after is None
    collector.observe(envelope(edit_event("large", before="x" * 65537)))
    large = ToolReturnPart("edit", {}, "large")
    collector.observe(envelope(FunctionToolResultEvent(large)))
    assert applied_edit(large).omitted


def test_provider_native_calls_and_returns_use_existing_transcript_tool_shapes() -> None:
    call = _response_part(NativeToolCallPart("web_search", {"query": "evidence"}, "native-one", provider_name="openai"))
    result = _response_part(
        NativeToolReturnPart("web_search", {"status": "completed"}, "native-one", provider_name="openai")
    )
    assert call.kind == "tool_call" and result.kind == "tool_result"
    assert call.provider == result.provider == "openai"
    assert call.tool_call_id == result.tool_call_id == "native-one"
    assert result.outcome == "success" and result.value == {"status": "completed"}
    huge = _response_part(NativeToolReturnPart("web_search", "x" * 300000, "large", provider_name="anthropic"))
    assert huge.value_omitted and huge.value is None
    anonymous_call = _response_part(NativeToolCallPart("web_search", {}, "shared-id"))
    anonymous_result = _response_part(NativeToolReturnPart("web_search", {}, "shared-id"))
    assert anonymous_call.provider == anonymous_result.provider == "provider"


def test_openai_adapter_native_search_failure_retains_provider_status() -> None:
    from openai.types.responses.response_function_web_search import ResponseFunctionWebSearch
    from pydantic_ai.models.openai import _map_web_search_tool_call

    _, returned = _map_web_search_tool_call(
        ResponseFunctionWebSearch(
            id="ws-failed", action={"type": "search", "query": "docs"}, status="failed", type="web_search_call"
        ),
        "openai",
    )
    result = _response_part(returned)
    # The presentation must inspect status, not assume the adapter's default outcome proves success.
    assert result.provider == "openai" and result.tool_name == "web_search"
    assert result.outcome == "success" and result.value == {"status": "failed"}


async def test_real_file_edit_evidence_survives_completed_run_and_app_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration = _write_configuration(tmp_path)
    agent = tmp_path / "agents" / "assistant.yaml"
    agent.write_text(
        agent.read_text()
        + "capabilities:\n  - capability: dynamic_environment\n    configuration: {files_enabled: true}\n"
    )
    path = tmp_path / "workspace" / "sample.txt"
    path.write_text("original\n")
    steps = 0

    async def resolve(self, context, model_id):
        async def model(messages, info):
            nonlocal steps
            steps += 1
            if steps == 1:
                yield {
                    0: DeltaToolCall(
                        name="edit",
                        json_args=json.dumps(
                            {"file_path": str(path), "old_string": "original", "new_string": "changed"}
                        ),
                        tool_call_id="edit-one",
                    )
                }
            else:
                yield "Edited"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})
    async with open_harness_ui_app(
        settings, configuration_path=configuration, host_mode="webui", instrumentation=None
    ) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Edit the sample")
        outcome = await app.wait_root_operation(receipt.receipt_id)
        assert outcome.status == "completed", outcome.model_dump_json()
        page = await app.get_thread_transcript(thread_id=thread.thread_id)
        assert path.read_text() == "changed\n", page.model_dump_json()
        results = [part for entry in page.entries for part in entry.parts if part.kind == "tool_result"]
        assert len(results) == 1
        assert results[0].applied_edit is not None, page.model_dump_json()
        assert results[0].applied_edit.before == "original\n"
        assert results[0].applied_edit.after == "changed\n"
        assert "before" not in results[0].value
    path.write_text("subsequent user change\n")
    async with open_harness_ui_app(
        settings, configuration_path=configuration, host_mode="webui", instrumentation=None
    ) as app:
        page = await app.get_thread_transcript(thread_id=thread.thread_id)
        saved = next(part for entry in page.entries for part in entry.parts if part.applied_edit is not None)
        assert saved.applied_edit.before == "original\n" and saved.applied_edit.after == "changed\n"
