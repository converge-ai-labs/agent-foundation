"""Service cache defaults reach Anthropic gateways as supported content-block markers."""

import json

import httpx2
import pytest
from a13n_service.providers.model_settings import effective_settings
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider


@pytest.mark.anyio
@pytest.mark.parametrize(
    "defaults,overrides,expected_blocks",
    [
        ({}, {}, {"system", "tools", "messages"}),
        ({"cache": False}, {}, set()),
        ({}, {"cache": False}, set()),
        ({"anthropic_cache_messages": False}, {}, set()),
        ({"anthropic_cache_instructions": True}, {}, {"system"}),
        ({"anthropic_cache_messages": True}, {"anthropic_cache_messages": False}, set()),
    ],
)
async def test_anthropic_cache_defaults_reach_wire(defaults, overrides, expected_blocks):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        message = {
            "id": "msg_fixture",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 0},
        }
        events = [
            {"type": "message_start", "message": message},
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
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events),
        )

    def lookup() -> str:
        """Look up a reference."""
        return "reference"

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = AnthropicModel("claude-sonnet-4-6", provider=AnthropicProvider(api_key="fixture", http_client=client))
        agent = Agent(
            model,
            instructions="Answer briefly.",
            tools=[lookup],
            model_settings=effective_settings("anthropic.messages", defaults, overrides),
        )
        assert (await agent.run("hello")).output == "Hello"
    assert len(bodies) == 1
    body = bodies[0]
    assert "cache_control" not in body
    blocks = {
        "system": body["system"][-1],
        "tools": body["tools"][-1],
        "messages": body["messages"][-1]["content"][-1],
    }
    assert {name for name, block in blocks.items() if "cache_control" in block} == expected_blocks
    for name in expected_blocks:
        assert blocks[name]["cache_control"]["type"] == "ephemeral"
