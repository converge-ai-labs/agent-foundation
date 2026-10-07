from __future__ import annotations

import copy
import json
from dataclasses import replace

import httpx2
import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.capabilities import ToolCallSource, ToolReviewConfig, ToolReviewRequest
from a13n_harness.models.codex import CodexRequestModel
from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability
from a13n_harness.tools.identity import TOOL_IDENTITY_KEY, ToolIdentity
from a13n_harness_ui.guardian_credits import GuardianParentCapability, GuardianReviewCapability
from pydantic_ai import RunContext, Tool
from pydantic_ai.capabilities import Capability
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openai_codex import OpenAICodexCredentials
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import RunUsage

pytestmark = pytest.mark.anyio


def _sse(response_id, name=None, arguments=None):
    response = {
        "id": response_id,
        "created_at": 1,
        "model": "gpt-5",
        "object": "response",
        "output": [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "status": "in_progress",
    }
    events = [{"type": "response.created", "response": response}]
    if name:
        item = {
            "type": "function_call",
            "id": f"fc_{response_id}",
            "call_id": f"call_{name}",
            "name": name,
            "arguments": "",
            "status": "in_progress",
        }
        events += [
            {"type": "response.output_item.added", "output_index": 0, "item": item},
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "item_id": item["id"],
                "delta": json.dumps(arguments),
            },
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {**item, "status": "completed", "arguments": json.dumps(arguments)},
            },
        ]
    else:
        item = {"type": "message", "id": "msg_done", "role": "assistant", "status": "in_progress", "content": []}
        events += [
            {"type": "response.output_item.added", "output_index": 0, "item": item},
            {
                "type": "response.content_part.added",
                "output_index": 0,
                "content_index": 0,
                "item_id": "msg_done",
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
            {
                "type": "response.output_text.delta",
                "output_index": 0,
                "content_index": 0,
                "item_id": "msg_done",
                "delta": "done",
            },
        ]
    events.append({"type": "response.completed", "response": {**response, "status": "completed"}})
    return "".join(
        f"data: {json.dumps({**event, 'sequence_number': i})}\n\n" for i, event in enumerate(events)
    ).encode()


class _Credentials:
    async def load(self):
        return OpenAICodexCredentials(
            account_id="test-account", access_token="test-token", refresh_token="test-refresh"
        )

    async def save(self, credentials):
        raise AssertionError("Mock credentials must not refresh")


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize("review_error", [False, True])
async def test_parent_and_default_reviewer_are_linked_on_wire_and_metered(codex, review_error):
    requests = []
    executed = []

    def handle(request):
        body = json.loads(request.content)
        requests.append((request, body))
        assert request.url.path.endswith("/responses")
        index = len(requests)
        if index == 2 and review_error:
            return httpx2.Response(400, json={"error": {"message": "Guardian request rejected"}})
        content = (
            _sse("resp_parent", "shell_exec", {"command": "echo test"})
            if index == 1
            else _sse("resp_review", "submit_tool_review", {"risk": "low"})
            if index == 2
            else _sse("resp_done")
        )
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=content)

    def shell_exec(command: str) -> str:
        executed.append(command)
        return "ok"

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        model = (
            CodexRequestModel("gpt-5.6-luna", credential_source=_Credentials(), http_client=client)
            if codex
            else OpenAIResponsesModel(
                "gpt-5",
                provider=OpenAIProvider(api_key="test", http_client=client),
                settings={"openai_service_tier": "priority", "service_tier": "priority"},
            )
        )

        async def resolve(ctx, model_id):
            return model  # Deliberately share the instance across parent and auxiliary calls.

        toolset = FunctionToolset(
            [Tool(shell_exec, metadata={TOOL_IDENTITY_KEY: ToolIdentity("environment.shell_exec")})]
        )
        settings = {
            "extra_headers": {"x-custom": "kept"},
            "extra_body": {"client_metadata": {"custom": "kept"}},
            "openai_service_tier": "priority",
        }
        original = copy.deepcopy(settings)
        executable = HarnessBuilder().build(
            AgentSpec(model="main", model_settings=settings),
            output_type=str,
            capabilities=(
                Capability(toolsets=[toolset]),
                GuardianParentCapability(),
                ToolPermissionsCapability(
                    ToolPermissions(rules={"environment.shell_exec": "review"}),
                    review=ToolReviewConfig(model="review", model_settings=settings, on_error="allow"),
                    review_capabilities=(GuardianReviewCapability(),),
                ),
            ),
        )
        result = await executable.run("test shell", bindings=RunBindings.embedded(model_resolver=resolve))
    assert result.output_or_raise() == "done"
    assert executed == ["echo test"]
    assert len(requests) == 3
    for i in (0, 2):
        request, body = requests[i]
        assert body["client_metadata"] == {"custom": "kept", "guardian_credits_requested": "true"}
        assert "x-codex-guardian" not in request.headers
        assert "x-openai-subagent" not in request.headers
        assert body["service_tier"] == "priority"
    request, body = requests[1]
    assert body["client_metadata"] == {
        "custom": "kept",
        "x-openai-subagent": "guardian",
        "parent_response_id": "resp_parent",
    }
    assert request.headers["x-codex-guardian"] == "reviewer"
    assert request.headers["x-openai-subagent"] == "guardian"
    assert request.headers["x-custom"] == "kept"
    assert "x-codex-routing-hint" not in request.headers
    assert "service_tier" not in body
    assert "resp_parent" not in json.dumps(body["input"])
    assert settings == original
    if not review_error:
        assert len([record for record in result.usage_records if record.source == "tool.review"]) == 1
    # Even rejection follows the configured policy without an unmarked retry or a different Model.
    assert len(requests) == 3


@pytest.mark.parametrize("profile,response_id", [("shell", None), ("general", "resp_parent"), ("shell", "resp_parent")])
async def test_review_metadata_is_request_local_and_only_links_shell_with_a_source(profile, response_id):
    model = OpenAIResponsesModel("gpt-5", provider=OpenAIProvider(api_key="test"))
    settings = {
        "extra_headers": {"X-Codex-Guardian": "old", "custom": "kept"},
        "extra_body": {
            "client_metadata": {"guardian_credits_requested": "true", "parent_response_id": "stale", "custom": "kept"}
        },
    }
    original = copy.deepcopy(settings)
    request = ToolReviewRequest(
        tool_id="environment.shell_exec",
        tool_name="shell_exec",
        tool_call_id="call-1",
        parameters_schema={},
        arguments={},
        profile=profile,
        source=ToolCallSource(provider_response_id=response_id),
    )
    ctx = RunContext(deps=request, model=model, usage=RunUsage())
    model_request = ModelRequestContext(
        model=model, messages=[], model_settings=settings, model_request_parameters=ModelRequestParameters()
    )
    updated = await GuardianReviewCapability().before_model_request(ctx, model_request)
    linked = profile == "shell" and response_id is not None
    metadata = updated.model_settings["extra_body"]["client_metadata"]
    assert metadata == (
        {"custom": "kept", "parent_response_id": response_id, "x-openai-subagent": "guardian"}
        if linked
        else {"custom": "kept"}
    )
    assert settings == original
    incompatible = replace(model_request, model=TestModel())
    assert await GuardianReviewCapability().before_model_request(ctx, incompatible) is incompatible
