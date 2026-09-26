from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness_ui.surfaces import ApprovalRequestView
from a13n_harness_ui.terminal_projection import TerminalProjectionService
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ToolCallPart


@pytest.mark.anyio
@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("oversized", ["none", "arguments", "metadata"])
async def test_approval_projection_preserves_omission_and_override_capabilities(bound, oversized):
    metadata = {"note": "x" * (70_000 if oversized == "metadata" else 5)}
    if bound:
        metadata["a13n.harness.tool-approval"] = {"tool_id": "custom.publish", "binding": "captured"}
    request = ToolCallPart("publish", {"value": "x" * (70_000 if oversized == "arguments" else 5)}, "approval")
    pending = DeferredToolRequests(approvals=[request], metadata={"approval": metadata})
    thread = SimpleNamespace(
        continuation=SimpleNamespace(logical_digest="a" * 64), read_model=SimpleNamespace(deferred_requests=pending)
    )
    projection = TerminalProjectionService(
        store=SimpleNamespace(threads=SimpleNamespace(get=AsyncMock(return_value=thread))),
        configurations=None,
        threads=None,
        root_runs=SimpleNamespace(interaction_expiry=AsyncMock(return_value=None)),
        children=None,
        configuration_path=None,
    )
    batch = await projection.decisions(thread_id="thread-one")
    assert batch is not None
    approval = batch.requests[0]
    assert isinstance(approval, ApprovalRequestView)
    assert approval.arguments_omitted is (oversized == "arguments")
    assert approval.metadata_omitted is (oversized == "metadata")
    assert approval.override_allowed is (oversized != "arguments")
    if oversized == "metadata":
        assert approval.metadata is None
    else:
        assert approval.metadata == metadata


@pytest.mark.anyio
@pytest.mark.parametrize("question", [False, True])
async def test_external_and_question_projection_disclose_metadata_omissions(question):
    args = (
        {
            "questions": [
                {
                    "header": "Pick",
                    "question": "Where?",
                    "options": [{"label": "Left", "description": "First"}, {"label": "Right", "description": "Second"}],
                }
            ]
        }
        if question
        else {}
    )
    request = ToolCallPart("ask_user_question" if question else "lookup", args, "call")
    pending = DeferredToolRequests(calls=[request], metadata={"call": {"note": "x" * 70_000}})
    thread = SimpleNamespace(
        continuation=SimpleNamespace(logical_digest="a" * 64), read_model=SimpleNamespace(deferred_requests=pending)
    )
    projection = TerminalProjectionService(
        store=SimpleNamespace(threads=SimpleNamespace(get=AsyncMock(return_value=thread))),
        configurations=None,
        threads=None,
        root_runs=SimpleNamespace(interaction_expiry=AsyncMock(return_value=None)),
        children=None,
        configuration_path=None,
    )
    batch = await projection.decisions(thread_id="thread-one")
    assert batch is not None
    assert batch.requests[0].metadata is None
    assert batch.requests[0].metadata_omitted
