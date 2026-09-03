from __future__ import annotations

import json

import httpx2
import pytest
from a13n_service.connectivity.ingress.providers.slack_client import (
    SlackActionBinding,
    SlackAutoReplyArguments,
    SlackForcedReplyArguments,
    SlackListMembersArguments,
    SlackNativeActionError,
    SlackNativeClient,
    SlackReadMessagesArguments,
    SlackReplyOutcomeUnknown,
    SlackReplySucceeded,
)
from pydantic import ValidationError

_BOT_TOKEN = "xoxb-private-token"


def _binding(*, reply_mode: str = "auto") -> SlackActionBinding:
    return SlackActionBinding.model_validate(
        {
            "channel_id": "C123",
            "root_thread_ts": "123.456",
            "conversation_kind": "channel",
            "reply_mode": reply_mode,
        }
    )


@pytest.mark.anyio
async def test_slack_reply_uses_only_hidden_binding_destination() -> None:
    seen: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            json={
                "ok": True,
                "channel": "C123",
                "ts": "124.000",
                "message": {"thread_ts": "123.456"},
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http_client:
        outcome = await SlackNativeClient(http_client).reply(
            _binding(),
            SlackAutoReplyArguments(text="hello", placement="thread"),
            bot_token=_BOT_TOKEN,
            request_id="req_1",
        )

    assert isinstance(outcome, SlackReplySucceeded)
    assert outcome.receipt.root_thread_ts == "123.456"
    assert len(seen) == 1
    assert seen[0].url == "https://slack.com/api/chat.postMessage"
    assert json.loads(seen[0].content) == {
        "channel": "C123",
        "text": "hello",
        "thread_ts": "123.456",
    }
    assert seen[0].headers["authorization"] == f"Bearer {_BOT_TOKEN}"


@pytest.mark.anyio
async def test_slack_model_arguments_cannot_select_destination_or_override_forced_placement() -> None:
    schema = json.dumps(
        {
            "forced_reply": SlackForcedReplyArguments.model_json_schema(),
            "auto_reply": SlackAutoReplyArguments.model_json_schema(),
            "members": SlackListMembersArguments.model_json_schema(),
            "messages": SlackReadMessagesArguments.model_json_schema(),
        },
        sort_keys=True,
    )

    for forbidden in ("channel_id", "team_id", "thread_ts", "token", "ingress_id"):
        assert forbidden not in schema
    assert "placement" not in json.dumps(SlackForcedReplyArguments.model_json_schema())
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _request: pytest.fail("request must not be sent"))
    ) as http_client:
        with pytest.raises(SlackNativeActionError, match="invalid_arguments"):
            await SlackNativeClient(http_client).reply(
                _binding(reply_mode="thread"),
                SlackAutoReplyArguments(text="hello", placement="main"),
                bot_token=_BOT_TOKEN,
                request_id="req_invalid",
            )


@pytest.mark.anyio
@pytest.mark.parametrize("response", [httpx2.Response(503), httpx2.Response(200, content=b"not-json")])
async def test_slack_reply_reports_unknown_after_ambiguous_response(response: httpx2.Response) -> None:
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: response)) as http_client:
        outcome = await SlackNativeClient(http_client).reply(
            _binding(),
            SlackAutoReplyArguments(text="hello"),
            bot_token=_BOT_TOKEN,
            request_id="req_unknown",
        )

    assert outcome == SlackReplyOutcomeUnknown(request_id="req_unknown")


@pytest.mark.anyio
async def test_slack_reply_does_not_hide_deterministic_rate_limit() -> None:
    response = httpx2.Response(429, headers={"retry-after": "30"}, json={"ok": False})
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: response)) as http_client:
        with pytest.raises(SlackNativeActionError) as raised:
            await SlackNativeClient(http_client).reply(
                _binding(),
                SlackAutoReplyArguments(text="hello"),
                bot_token=_BOT_TOKEN,
                request_id="req_rate",
            )

    assert raised.value.code == "rate_limited"
    assert raised.value.retry_after_seconds == 30


@pytest.mark.anyio
async def test_slack_reads_are_bounded_and_return_provider_cursor() -> None:
    responses = iter(
        (
            httpx2.Response(
                200,
                json={
                    "ok": True,
                    "members": ["U1", "U2"],
                    "response_metadata": {"next_cursor": "next-members"},
                },
            ),
            httpx2.Response(
                200,
                json={
                    "ok": True,
                    "messages": [{"ts": "125.000", "user": "U1", "text": "safe"}],
                    "response_metadata": {"next_cursor": "next-messages"},
                },
            ),
        )
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: next(responses))) as http_client:
        client = SlackNativeClient(http_client)
        members = await client.list_members(
            _binding(),
            SlackListMembersArguments(limit=2),
            bot_token=_BOT_TOKEN,
        )
        messages = await client.read_messages(
            _binding(),
            SlackReadMessagesArguments(scope="discussion", limit=1),
            bot_token=_BOT_TOKEN,
        )

    assert tuple(item.user_id for item in members.items) == ("U1", "U2")
    assert members.cursor == "next-members"
    assert messages.items[0].text == "safe"
    assert messages.cursor == "next-messages"
    with pytest.raises(ValidationError):
        SlackReadMessagesArguments(scope="conversation", limit=16)


@pytest.mark.anyio
async def test_slack_response_and_secret_representations_are_bounded() -> None:
    response = httpx2.Response(200, content=b"x" * 65, headers={"content-length": "65"})
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: response)) as http_client:
        client = SlackNativeClient(http_client, response_max_bytes=64)
        with pytest.raises(SlackNativeActionError, match="response_too_large"):
            await client.list_members(
                _binding(),
                SlackListMembersArguments(),
                bot_token=_BOT_TOKEN,
            )

    assert "C123" not in repr(_binding())
    assert "123.456" not in repr(_binding())
    assert "hello" not in repr(SlackForcedReplyArguments(text="hello"))
