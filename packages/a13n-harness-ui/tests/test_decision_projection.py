from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness import DeferredToolCall, DeferredToolRequests
from a13n_harness_ui.surfaces import ApprovalRequestView
from a13n_harness_ui.terminal_projection import TerminalProjectionService
from pydantic_ai.messages import ToolCallPart


@pytest.mark.anyio
@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("forwarded", [False, True])
@pytest.mark.parametrize("oversized", ["none", "arguments", "metadata"])
async def test_approval_projection_preserves_omission_and_override_capabilities(bound, oversized, forwarded):
    metadata = {"note": "x" * (70_000 if oversized == "metadata" else 5)}
    if bound:
        metadata["a13n.harness.tool-approval"] = {"tool_id": "custom.publish", "binding": "captured"}
    request = ToolCallPart("publish", {"value": "x" * (70_000 if oversized == "arguments" else 5)}, "approval")
    model = (
        ToolCallPart("call_tool", {"group": "tools", "tool": "publish", "arguments": request.args}, "model")
        if forwarded
        else request
    )
    pending = DeferredToolRequests(
        approvals=[DeferredToolCall(model, request, "tool/test/publish")], metadata={"approval": metadata}
    )
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
    assert approval.tool_name == "publish" and approval.request_id == "approval"
    assert approval.arguments_omitted is (oversized == "arguments")
    assert approval.metadata_omitted is (oversized == "metadata")
    assert approval.override_allowed is (oversized != "arguments")
    if oversized == "metadata":
        assert approval.metadata is None
    else:
        assert approval.metadata == metadata


@pytest.mark.anyio
@pytest.mark.parametrize("question", [False, True])
@pytest.mark.parametrize("forwarded", [False, True])
async def test_external_and_question_projection_disclose_metadata_omissions(question, forwarded):
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
    name = "ask_user_question" if question else "lookup"
    request = ToolCallPart("clients__" + name if forwarded else name, args, "call")
    pending = DeferredToolRequests(
        calls=[
            DeferredToolCall(
                ToolCallPart("call_tool", {}, "model") if forwarded else request, request, "tool/test/" + name
            )
        ],
        metadata={
            "call": {"note": "x" * 70_000, "a13n.harness.client-tool": {"declared_name": name, "toolset_id": "editor"}}
        },
    )
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
    assert batch.requests[0].tool_name == name and batch.requests[0].request_id == "call"
    assert batch.requests[0].kind == ("question" if question else "external")
    assert batch.requests[0].metadata is None
    assert batch.requests[0].metadata_omitted


@pytest.mark.anyio
@pytest.mark.parametrize("proxy", [False, True])
@pytest.mark.parametrize("approval", [False, True])
@pytest.mark.parametrize("length", [120, 256])
async def test_long_tool_names_survive_pending_and_decision_projection(proxy, approval, length):
    import json

    from a13n_harness import HarnessBuilder
    from a13n_harness.capabilities import ProxyToolSearchConfig, ToolSearchPlan, ToolSearchSelection
    from a13n_harness.tools.client import (
        ClientToolDefinition,
        ClientToolsCapability,
        ClientToolsetDefinition,
        ClientToolsSpec,
    )
    from a13n_harness_ui.thread_projection import _deferred_requests, _request_parts, _response_part
    from pydantic_ai import Tool
    from pydantic_ai.agent.spec import AgentSpec
    from pydantic_ai.capabilities import Capability
    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel
    from pydantic_ai.toolsets import FunctionToolset

    name = "lookup_" + "x" * (length - 7)

    def lookup():
        pytest.fail("A pending approval must not execute")

    source = (
        Capability(toolsets=[FunctionToolset([Tool(lookup, name=name, requires_approval=True)], id="editor")])
        if approval
        else ClientToolsCapability(
            spec=ClientToolsSpec(
                default_toolsets=(
                    ClientToolsetDefinition(
                        toolset_id="editor",
                        tools=(
                            ClientToolDefinition(
                                name=name, description="Lookup", parameters_json_schema={"type": "object"}
                            ),
                        ),
                    ),
                )
            )
        )
    )

    async def model(messages, info):
        yield {
            0: DeltaToolCall(
                name="call_tool" if proxy else name,
                json_args=json.dumps({"group": "workspace", "tool": name, "arguments": {}} if proxy else {}),
                tool_call_id="model-call",
            )
        }

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=[source],
        tool_search=ToolSearchPlan(
            groups={"workspace": ToolSearchSelection("Workspace", capabilities=(source,))},
            config=ProxyToolSearchConfig(),
        )
        if proxy
        else None,
    )
    result = await executable.run("go")
    assert result.status == "suspended" and result.deferred is not None
    pending = result.deferred
    target = (pending.approvals if approval else pending.calls)[0].target_call
    qualified_name = "workspace__" + name if proxy else name
    (detail_request,) = _deferred_requests(pending)
    assert detail_request.tool_name == qualified_name
    assert detail_request.request_id == target.tool_call_id
    assert detail_request.kind == ("approval" if approval else "external")
    assert _response_part(target).tool_name == qualified_name
    (returned,) = _request_parts(ToolReturnPart(qualified_name, "done", tool_call_id=target.tool_call_id))
    assert returned.tool_name == qualified_name and returned.tool_call_id == target.tool_call_id
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
    (decision,) = batch.requests
    assert decision.tool_name == (qualified_name if approval else name)
    assert decision.request_id == target.tool_call_id
