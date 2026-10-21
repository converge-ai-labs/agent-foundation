"""Exercise native request serialization at the Cloudflare boundary."""

import json

import httpx2
import pytest
from a13n_harness.providers.model.cloudflare import DEFINITION
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tools import ToolDefinition


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["openai/gpt-5.4", "anthropic/claude-sonnet-4.6", "google/gemini-3-flash"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_unified_requests(name, streaming, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ambient-token")
    calls = []

    async def handler(request):
        calls.append(request)
        assert (
            str(request.url).partition("?")[0]
            == f"https://api.cloudflare.com/client/v4/accounts/{'a' * 32}/ai/v1/chat/completions"
        )
        assert request.headers["authorization"] == "Bearer cf-token"
        assert "ambient" not in str(request.headers)
        assert "x-api-key" not in request.headers
        assert request.headers["cf-aig-gateway-id"] == "agent-tests"
        assert request.headers["x-team"] == "test"
        body = json.loads(request.content)
        assert body["model"] == name
        assert body["tools"]
        if streaming:
            assert body["stream"] is True
        # Terminate after inspecting the real SDK serialization, before parsing a response.
        return httpx2.Response(401, json={"error": {"message": "fixture", "type": "authentication_error"}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = await DEFINITION.build(
            name,
            configuration={"account_id": "a" * 32, "gateway_id": "agent-tests"},
            credential={"api_key": "cf-token"},
            http_client=client,
            extra_headers={"x-team": "test"},
        )
        assert model.model_name == name
        assert not model.profile["supported_native_tools"]
        if name.startswith("openai/"):
            assert model.profile["openai_supports_reasoning"]
        parameters = ModelRequestParameters(
            function_tools=[ToolDefinition(name="lookup", parameters_json_schema={"type": "object", "properties": {}})]
        )
        messages = [ModelRequest(parts=[UserPromptPart("Hello")])]
        from pydantic_ai.exceptions import ModelHTTPError

        async with model:
            with pytest.raises(ModelHTTPError, match="401"):
                if streaming:
                    async with model.request_stream(messages, None, parameters):
                        pass
                else:
                    await model.request(messages, None, parameters)
        assert not client.is_closed
    assert len(calls) == 1


@pytest.mark.parametrize(
    "config", [{}, {"account_id": "../other"}, {"account_id": "a" * 32, "gateway_id": "bad\r\nheader"}]
)
def test_invalid_configuration(config):
    with pytest.raises(ValueError):
        DEFINITION.bind(config, {"api_key": "token"})


@pytest.mark.parametrize("header", ["Authorization", "X-Api-Key", "cf-aig-gateway-id"])
def test_reserved_headers(header):
    with pytest.raises(ValueError):
        DEFINITION.bind({"account_id": "a" * 32}, {"api_key": "token"}, extra_headers={header: "other"})


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["openai/gpt-5.4", "anthropic/claude-sonnet-4.6", "google/gemini-3-flash"])
async def test_streamed_tool_call_and_followup_keep_gateway_identity(name):
    from pydantic_ai import Agent
    from pydantic_ai.run import AgentRunResultEvent

    bodies = []

    async def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert body["model"] == name
        assert body["stream"] is True
        assert "cf-aig-gateway-id" not in request.headers
        first = len(bodies) == 1
        delta = (
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_lookup",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{}"},
                    }
                ]
            }
            if first
            else {"content": "Found it"}
        )
        chunks = [
            {
                "id": "chat_fixture",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": body["model"],
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": "chat_fixture",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": body["model"],
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if first else "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            },
        ]
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n",
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = await DEFINITION.build(
            name,
            configuration={"base_url": "https://gateway.example/ai/"},
            credential={"api_key": "cf-token"},
            http_client=client,
        )
        agent = Agent(model)

        @agent.tool_plain
        def lookup() -> str:
            return "record 42"

        async with agent.run_stream_events("Look it up") as stream:
            events = [event async for event in stream]
        assert isinstance(events[-1], AgentRunResultEvent)
        assert events[-1].result.output == "Found it"
        assert events[-1].result.usage.requests == 2
    assert len(bodies) == 2
    assert any(message.get("role") == "tool" and message["content"] == "record 42" for message in bodies[1]["messages"])


@pytest.mark.parametrize(
    "path", ["/ai/v1", "/ai/v1/", "/ai/v1/chat/completions", "/v1", "/ai?team=a", "/ai#section", "/ai?", "/ai#"]
)
def test_configuration_rejects_wrong_api_root_even_with_account_id(path):
    with pytest.raises(ValueError, match="must end in /ai"):
        DEFINITION.bind({"base_url": f"https://gateway.example{path}", "account_id": "a" * 32}, {"api_key": "token"})


@pytest.mark.parametrize("path", ["/ai", "/ai/", "/proxy/ai"])
def test_configuration_accepts_api_root_and_optional_trailing_slash(path):
    endpoint = f"https://gateway.example{path}"
    assert DEFINITION.bind({"base_url": endpoint}, {"api_key": "token"}).endpoint == endpoint
