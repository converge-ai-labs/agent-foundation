from __future__ import annotations

import json

import httpx2
import pytest
from a13n_service.connectivity.providers.slack.client import (
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
                    "user": {
                        "id": "U1",
                        "name": "one",
                        "is_bot": False,
                        "deleted": False,
                        "profile": {"display_name": "One"},
                    },
                },
            ),
            httpx2.Response(
                200,
                json={
                    "ok": True,
                    "user": {
                        "id": "U2",
                        "name": "two",
                        "is_bot": True,
                        "deleted": False,
                        "profile": {"display_name": ""},
                    },
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
    assert tuple(item.display_name for item in members.items) == ("One", "two")
    assert tuple(item.is_bot for item in members.items) == (False, True)
    assert members.cursor == "next-members"
    assert messages.items[0].text == "safe"
    assert messages.cursor == "next-messages"
    with pytest.raises(ValidationError):
        SlackReadMessagesArguments(scope="conversation", limit=16)


@pytest.mark.anyio
async def test_slack_member_enrichment_cache_is_bounded_and_scoped_by_token() -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        operation = request.url.path.rsplit("/", maxsplit=1)[-1]
        payload = json.loads(request.content)
        if operation == "conversations.members":
            return httpx2.Response(200, json={"ok": True, "members": ["U1"]})
        return httpx2.Response(
            200,
            json={
                "ok": True,
                "user": {
                    "id": payload["user"],
                    "name": request.headers["authorization"],
                    "profile": {"display_name": "cached"},
                },
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http_client:
        client = SlackNativeClient(http_client, member_cache_max_entries=1)
        arguments = SlackListMembersArguments(limit=1)
        await client.list_members(_binding(), arguments, bot_token="token-one")
        await client.list_members(_binding(), arguments, bot_token="token-one")
        await client.list_members(_binding(), arguments, bot_token="token-two")
        await client.list_members(_binding(), arguments, bot_token="token-one")

    info_requests = [request for request in requests if request.url.path.endswith("/users.info")]
    assert len(info_requests) == 3
    assert "token-one" not in repr(client._member_cache)
    assert "token-two" not in repr(client._member_cache)


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


@pytest.mark.anyio
async def test_slack_inspection_binds_bot_user_app_and_team_without_posting() -> None:
    seen: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if request.url.path.endswith("auth.test"):
            return httpx2.Response(
                200, json={"ok": True, "team_id": "T1", "team": "Acme", "user_id": "U1", "bot_id": "B1"}
            )
        assert request.method == "GET"
        assert request.url.params["bot"] == "B1"
        return httpx2.Response(
            200,
            json={"ok": True, "bot": {"id": "B1", "user_id": "U1", "app_id": "A1", "name": "Helper", "deleted": False}},
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http_client:
        identity = await SlackNativeClient(http_client).inspect_installation(bot_token=_BOT_TOKEN)
    assert (identity.app_id, identity.organization_id, identity.bot_id) == ("A1", "T1", "U1")
    assert identity.enabled
    assert len(seen) == 2
    assert _BOT_TOKEN not in repr(identity)


@pytest.mark.anyio
async def test_slack_inspection_rejects_different_bot_user() -> None:
    replies = iter(
        (
            {"ok": True, "team_id": "T1", "team": "Acme", "user_id": "U1", "bot_id": "B1"},
            {"ok": True, "bot": {"id": "B1", "user_id": "U2", "app_id": "A1", "name": "Helper", "deleted": False}},
        )
    )
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=next(replies)))
    ) as http_client:
        with pytest.raises(SlackNativeActionError, match="invalid_provider_response"):
            await SlackNativeClient(http_client).inspect_installation(bot_token=_BOT_TOKEN)


@pytest.mark.anyio
@pytest.mark.parametrize("member", [True, False, None])
async def test_slack_conversation_inspection_preserves_membership_uncertainty(member: bool | None) -> None:
    channel = {
        "id": "C1",
        "name": "engineering",
        "is_private": True,
        "is_im": False,
        "is_mpim": False,
        "is_archived": False,
        "is_ext_shared": False,
    }
    if member is not None:
        channel["is_member"] = member

    def respond(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "GET"
        assert request.url.params["channel"] == "C1"
        return httpx2.Response(200, json={"ok": True, "channel": channel})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http_client:
        observation = await SlackNativeClient(http_client).inspect_conversation("C1", bot_token=_BOT_TOKEN)
    assert observation.is_member is member
    assert observation.audience == "private"
    assert observation.is_active is True


@pytest.mark.anyio
async def test_slack_discovery_is_paginated_and_does_not_claim_membership() -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/users.conversations"
        assert request.url.params["cursor"] == "next-page"
        assert request.url.params["limit"] == "2"
        return httpx2.Response(
            200,
            json={
                "ok": True,
                "channels": [{"id": "C1", "name": "engineering"}],
                "response_metadata": {"next_cursor": "after"},
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http_client:
        page = await SlackNativeClient(http_client).list_conversations(
            bot_token=_BOT_TOKEN, limit=2, cursor="next-page"
        )
    assert page.cursor == "after"
    assert page.items[0].model_dump() == {"id": "C1", "name": "engineering"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {"ok": True, "channel": "C-other", "ts": "2.0"},
        {"ok": True, "channel": "C1", "ts": ""},
        {"ok": True, "channel": "C1", "ts": "2.0", "message": {"thread_ts": "different-root"}},
    ],
)
async def test_reply_does_not_confirm_a_malformed_or_mismatched_receipt(payload):
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=payload))) as http:
        outcome = await SlackNativeClient(http).reply(
            _binding(), SlackAutoReplyArguments(text="hello"), bot_token=_BOT_TOKEN, request_id="req_receipt"
        )
    assert outcome == SlackReplyOutcomeUnknown(request_id="req_receipt")
