"""Native MCP exposes content arguments while retaining the admitted target."""

import json

import httpx2
import pytest
from a13n_service.connectivity.native_actions import native_actions
from a13n_service.connectivity.toolsets import selected_tools
from a13n_service.endpoint_policy import EndpointPolicy
from pydantic import ValidationError

pytestmark = pytest.mark.anyio


async def test_slack_native_reply_hides_target_and_preserves_typed_unknown_outcome():
    requests = []

    def send(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx2.Response(200, json={"ok": True, "channel": "C-bound", "ts": "2.0"})
        raise httpx2.ReadTimeout("response lost after dispatch")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(send)) as http:
        actions = native_actions(
            "slack",
            {"channel_id": "C-bound", "root_thread_ts": "1.0", "conversation_kind": "channel"},
            {"reply_mode": "thread"},
            {},
            {"bot_token": "secret"},
            http,
            EndpointPolicy(),
        )
        reply = actions["slack.reply"]
        schema = reply.definition.inputSchema
        assert set(schema["properties"]) == {"text"}
        assert "C-bound" not in json.dumps(schema) and "secret" not in json.dumps(schema)
        selected_tools([item.definition for item in actions.values()], ("slack.reply",))
        with pytest.raises(ValidationError):
            await reply.call({"text": "hello", "channel_id": "C-other"})
        assert not requests
        assert await reply.call({"text": "hello"}) == {"kind": "succeeded"}
        unknown = await reply.call({"text": "second"})
        assert unknown["kind"] == "outcome_unknown"
        assert len(requests) == 2
        assert all(item["channel"] == "C-bound" and item["thread_ts"] == "1.0" for item in requests)


async def test_native_auto_reply_only_adds_placement_choice():
    async with httpx2.AsyncClient() as http:
        actions = native_actions(
            "slack",
            {"channel_id": "C-bound", "root_thread_ts": "1.0", "conversation_kind": "channel"},
            {"reply_mode": "auto"},
            {},
            {"bot_token": "secret"},
            http,
            EndpointPolicy(),
        )
        assert set(actions["slack.reply"].definition.inputSchema["properties"]) == {"text", "placement"}
