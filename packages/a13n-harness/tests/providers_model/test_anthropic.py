import json

import httpx2
import pytest
from a13n_harness.providers.model.anthropic import DEFINITION
from pydantic_ai.messages import ModelRequest, TextPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters


@pytest.mark.anyio
@pytest.mark.parametrize("base_url", [None, "https://gateway.example/custom"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_anthropic_sends_one_api_key_in_both_authentication_headers(base_url, streaming, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ambient-token")
    requests = []

    async def handler(request):
        requests.append(request)
        assert str(request.url).partition("?")[0] == f"{base_url or 'https://api.anthropic.com'}/v1/messages"
        assert request.headers["x-api-key"] == "fixture-key"
        assert request.headers["authorization"] == "Bearer fixture-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert request.headers["x-routing-key"] == "team"
        body = json.loads(request.content)
        assert body["model"] == "claude-test"
        assert body.get("stream", False) is streaming
        message = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-test",
            "content": [{"type": "text", "text": "Hello"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 4, "output_tokens": 1},
        }
        if not streaming:
            return httpx2.Response(200, json=message)
        events = [
            {"type": "message_start", "message": {**message, "content": [], "stop_reason": None}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}},
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
            {"type": "message_stop"},
        ]
        content = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=content)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = await DEFINITION.build(
            "claude-test",
            configuration={"base_url": base_url} if base_url else {},
            credential={"api_key": "fixture-key"},
            extra_headers={"x-routing-key": "team"},
            http_client=client,
        )
        messages = [ModelRequest(parts=[UserPromptPart("Hi")])]
        async with model:
            if streaming:
                async with model.request_stream(messages, None, ModelRequestParameters()) as stream:
                    async for _ in stream:
                        pass
                    response = stream.get()
            else:
                response = await model.request(messages, None, ModelRequestParameters())
        assert response.parts == [TextPart("Hello")]
        assert not client.is_closed
    assert len(requests) == 1


@pytest.mark.parametrize("header", ["authorization", "Authorization", "x-api-key", "X-Api-Key"])
def test_anthropic_authentication_headers_cannot_be_overridden(header):
    with pytest.raises(ValueError, match="authentication"):
        DEFINITION.bind({}, {"api_key": "fixture-key"}, extra_headers={header: "other-key"})
    with pytest.raises(ValueError, match="authentication"):
        DEFINITION.bind({"session_affinity_header": header}, {"api_key": "fixture-key"})
