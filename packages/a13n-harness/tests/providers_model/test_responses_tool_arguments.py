"""Exercise native Responses reconciliation through Harness-owned model adapters."""

import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx2
import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.models.chatgpt import OpenAIChatGPTResponsesModel
from a13n_harness.models.codex import CodexRequestModel
from a13n_harness.providers.model.chatgpt import OpenAIChatGPTProvider
from a13n_harness.providers.model.oauth.chatgpt import SCOPES, OpenAIChatGPTCredentials
from a13n_harness.providers.model.oauth.models import GrokCredentials
from a13n_harness.providers.model.oauth.runtime import build_grok_model
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelRequest, PartStartEvent, ToolCallPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openai_codex import OpenAICodexCredentials
from pydantic_ai.tools import ToolDefinition

pytestmark = pytest.mark.anyio

_ARGUMENTS = '{"value":"hello"}'


def _model(route, client):
    if route == "codex":
        source = AsyncMock()
        source.load.return_value = OpenAICodexCredentials(
            account_id="synthetic-account", access_token="synthetic-access", refresh_token="synthetic-refresh"
        )
        return CodexRequestModel("gpt-5", credential_source=source, http_client=client)
    if route == "chatgpt":
        source = AsyncMock()
        source.load.return_value = OpenAIChatGPTCredentials(
            subject="synthetic-subject",
            client_id="synthetic-client",
            ext_agent_host_id="synthetic-host",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=SCOPES,
            access_token="synthetic-access",
            refresh_token="synthetic-refresh",
            id_token="synthetic-id",
        )
        return OpenAIChatGPTResponsesModel(
            "gpt-5", provider=OpenAIChatGPTProvider(credential_source=source, http_client=client)
        )
    if route == "grok":
        source = AsyncMock()
        source.load.return_value = GrokCredentials(
            account_id="synthetic-account",
            auth_mode="oidc",
            create_time=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            issuer="https://issuer.example",
            client_id="synthetic-client",
            access_token="synthetic-access",
        )
        return build_grok_model("grok-4", credential_source=source, http_client=client)
    assert route == "openai"
    return OpenAIResponsesModel("gpt-5", provider=OpenAIProvider(api_key="synthetic", http_client=client))


def _sse(scenario, *, text=False):
    response = {"id": "resp_test", "object": "response", "model": "gpt-5", "created_at": 1, "output": []}
    events = [{"type": "response.created", "response": {**response, "status": "in_progress"}}]
    if text:
        events.append(
            {
                "type": "response.output_text.delta",
                "item_id": "msg_test",
                "output_index": 0,
                "content_index": 0,
                "delta": "finished",
            }
        )
    else:
        item = {"type": "function_call", "id": "fc_test", "call_id": "call_test", "name": "echo", "arguments": ""}
        if scenario != "item-only":
            events.append({"type": "response.output_item.added", "output_index": 0, "item": item})
        delta = {"type": "response.function_call_arguments.delta", "item_id": "fc_test", "output_index": 0}
        if scenario == "partial":
            events.append({**delta, "delta": _ARGUMENTS[:10]})
        elif scenario in ("duplicate", "disagree"):
            events.append({**delta, "delta": _ARGUMENTS})
        snapshot = '{"value":"different"}' if scenario == "disagree" else _ARGUMENTS
        if scenario not in ("output-done", "item-only"):
            events.append(
                {
                    "type": "response.function_call_arguments.done",
                    "item_id": "fc_test",
                    "output_index": 0,
                    "name": "echo",
                    "arguments": snapshot,
                }
            )
        done = {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {**item, "arguments": "" if scenario == "arguments-done" else snapshot, "status": "completed"},
        }
        events.append(done)
        if scenario == "duplicate":
            # Neither a repeated terminal snapshot nor a late delta may append arguments again.
            events.extend([done, {**delta, "delta": _ARGUMENTS}])
    events.append({"type": "response.completed", "response": {**response, "status": "completed"}})
    return "".join(f"data: {json.dumps({**event, 'sequence_number': index})}\n\n" for index, event in enumerate(events))


def _http_response(scenario, *, text=False):
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(scenario, text=text))


@pytest.mark.parametrize("route", ["openai", "codex", "chatgpt", "grok"])
@pytest.mark.parametrize("scenario", ["arguments-done", "output-done", "partial", "duplicate", "disagree", "item-only"])
async def test_streamed_tool_arguments_reconcile_once_through_model_adapters(route, scenario):
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: _http_response(scenario))) as client:
        model = _model(route, client)
        parameters = ModelRequestParameters(
            function_tools=[ToolDefinition(name="echo", parameters_json_schema={"type": "object"})]
        )
        warning = pytest.warns(UserWarning, match="arguments") if scenario == "disagree" else nullcontext()
        async with model:
            with warning:
                async with model.request_stream(
                    [ModelRequest(parts=[UserPromptPart("hello")])], None, parameters
                ) as stream:
                    events = [event async for event in stream]
                    response = stream.get()
        calls = [part for part in response.parts if isinstance(part, ToolCallPart)]
        assert len(calls) == 1
        assert calls[0].tool_name == "echo"
        assert calls[0].tool_call_id == "call_test"
        assert calls[0].args_as_dict() == {"value": "hello"}
        assert sum(isinstance(event, PartStartEvent) and isinstance(event.part, ToolCallPart) for event in events) == 1
        assert not client.is_closed


@pytest.mark.parametrize("route", ["codex", "chatgpt"])
@pytest.mark.parametrize("scenario", ["arguments-done", "output-done"])
async def test_forced_stream_collector_preserves_done_only_arguments(route, scenario):
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: _http_response(scenario))) as client:
        model = _model(route, client)
        async with model:
            response = await model.request(
                [ModelRequest(parts=[UserPromptPart("hello")])],
                None,
                ModelRequestParameters(
                    function_tools=[ToolDefinition(name="echo", parameters_json_schema={"type": "object"})]
                ),
            )
        assert len(response.parts) == 1
        assert isinstance(response.parts[0], ToolCallPart)
        assert response.parts[0].args_as_dict() == {"value": "hello"}


@pytest.mark.parametrize("route", ["codex", "chatgpt"])
@pytest.mark.parametrize("scenario", ["arguments-done", "output-done"])
async def test_harness_executes_and_replays_done_only_arguments_once(route, scenario):
    bodies = []
    calls = []

    def respond(request):
        bodies.append(json.loads(request.content))
        assert len(bodies) <= 2
        return _http_response(scenario, text=len(bodies) == 2)

    def echo(value: str) -> str:
        calls.append(value)
        return value

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        executable = HarnessBuilder().build(
            AgentSpec(),
            model=_model(route, client),
            output_type=str,
            capabilities=(Capability(id="tools", tools=[echo]),),
        )
        result = await executable.run("Echo hello", bindings=RunBindings.embedded())
        assert result.output_or_raise() == "finished"
    assert calls == ["hello"]
    assert len(bodies) == 2
    replay = next(item for item in bodies[1]["input"] if item.get("type") == "function_call")
    assert json.loads(replay["arguments"]) == {"value": "hello"}
    assert replay["call_id"] == "call_test"
    tool_return = next(item for item in bodies[1]["input"] if item.get("type") == "function_call_output")
    assert tool_return["call_id"] == "call_test"
