"""Deferred facts close once, before replacement history and Host publication."""

import json
from types import SimpleNamespace

import pytest
from a13n_harness.capabilities import HandoffCapability
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.storage import StoredContinuation
from a13n_harness_ui.surfaces import DecisionResponseBatch, ExternalToolResult, RootOperationStatus
from pydantic_ai import Tool
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets.external import ExternalToolset

from .test_app import _reconstructed, _settings, _write_configuration

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("submission", ["prompt", "partial", "empty"])
@pytest.mark.parametrize("fail_after_handoff", [False, True])
async def test_mixed_batch_continues_through_handoff_and_reopens(tmp_path, submission, fail_after_handoff):
    calls = 0
    effects = []
    returns = []
    provider_messages = []

    async def change():
        effects.append("changed")
        return "changed"

    async def model(messages, info):
        nonlocal calls
        calls += 1
        provider_messages.append(messages)
        if calls == 1:
            yield {
                0: DeltaToolCall(name="external", json_args="{}", tool_call_id="external-1"),
                1: DeltaToolCall(name="change", json_args="{}", tool_call_id="approval-1"),
            }
        elif calls == 2:
            returns.extend(
                part
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            )
            yield {
                0: DeltaToolCall(name="summarize", json_args='{"content":"Continue safely."}', tool_call_id="summary")
            }
        elif calls == 3 and fail_after_handoff:
            raise RuntimeError("provider failed after handoff")
        else:
            yield "done"

    def reconstruct(composition, *, root_capabilities=(), **kwargs):
        return _reconstructed(
            model,
            (
                *root_capabilities,
                HandoffCapability(),
                Capability(
                    id="mixed",
                    tools=[Tool(change, requires_approval=True)],
                    toolsets=[
                        ExternalToolset(
                            [ToolDefinition(name="external", parameters_json_schema={"type": "object"})], id="external"
                        )
                    ],
                ),
            ),
        )

    settings = _settings(tmp_path / "state")
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        app._root_runs._executor._agents = SimpleNamespace(reconstruct=reconstruct)
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="start")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.suspended
        detail = await app.get_thread(thread.thread_id)
        assert "run" in detail.available_actions and "respond" in detail.available_actions
        assert detail.continuation_id is not None
        pending = app._root_runs._interaction_waits[thread.thread_id]
        if submission == "prompt":
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Use my additional instruction")
        else:
            receipt = await app.respond_decisions(
                thread_id=thread.thread_id,
                response=DecisionResponseBatch(
                    expected_continuation_id=detail.continuation_id,
                    responses=(ExternalToolResult(request_id="external-1", result="provided"),)
                    if submission == "partial"
                    else (),
                ),
            )
        result = await app.wait_root_operation(receipt.receipt_id)
        assert result.status is (RootOperationStatus.failed if fail_after_handoff else RootOperationStatus.completed)
        assert pending.cancelled.is_set()
        assert await app.thread_decisions(thread_id=thread.thread_id) is None
        assert calls == 3
        assert effects == []
        by_id = {part.tool_call_id: part for part in returns}
        assert len(returns) == 2
        assert by_id["approval-1"].outcome == "denied"
        assert by_id["external-1"].outcome == ("success" if submission == "partial" else "failed")
        if submission == "prompt":
            assert "Use my additional instruction" in str(provider_messages[1])
        # Saving and error cleanup must not revalidate the original accepted batch
        # against the request-only history produced by handoff.
        stored_thread = await app._store.threads.get(thread.thread_id)
        assert stored_thread is not None and stored_thread.continuation is not None
        saved = await app._store.objects.read_model(stored_thread.continuation, StoredContinuation)
        assert saved.accepted_input is None
        assert not any(
            part.tool_call_id in {"external-1", "approval-1"}
            for message in saved.harness_state.message_history
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        )
        if fail_after_handoff:
            assert not any(isinstance(message, ModelResponse) for message in saved.harness_state.message_history)
        assert "Continue safely" in json.dumps(saved.harness_state.model_dump(mode="json"))
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        app._root_runs._executor._agents = SimpleNamespace(reconstruct=reconstruct)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Continue after restart")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        assert effects == []
