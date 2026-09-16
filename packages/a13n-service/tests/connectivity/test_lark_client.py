from __future__ import annotations

import json

import anyio
import httpx2
import pytest
from a13n_service.connectivity.providers.lark.actions import (
    LarkActionBinding,
    LarkAutoReplyArguments,
    LarkForcedReplyArguments,
    LarkListMembersArguments,
    LarkReadMessagesArguments,
    LarkReplyOutcomeUnknown,
    LarkReplySucceeded,
    LarkTextContent,
)
from a13n_service.connectivity.providers.lark.api import LarkApiError
from a13n_service.connectivity.providers.lark.client import LarkNativeClient
from a13n_service.connectivity.providers.lark.token import LarkTenantTokenProvider
from pydantic import ValidationError

_APP_SECRET = "private-app-secret"


class _AllowEndpoint:
    def __init__(self) -> None:
        self.calls = 0

    async def validate(self, endpoint: str, *, resolve_dns: bool = True) -> str:
        assert resolve_dns is True
        self.calls += 1
        return endpoint.rstrip("/")


def _binding(*, reply_mode: str = "thread") -> LarkActionBinding:
    return LarkActionBinding.model_validate(
        {
            "chat_id": "oc_chat",
            "message_id": "om_message",
            "discussion_id": "omt_thread",
            "chat_type": "group",
            "reply_mode": reply_mode,
        }
    )


def _client(
    http_client: httpx2.AsyncClient,
    endpoint: _AllowEndpoint,
    *,
    response_max_bytes: int = 1024 * 1024,
) -> tuple[LarkNativeClient, LarkTenantTokenProvider]:
    tokens = LarkTenantTokenProvider(
        http_client,
        endpoint,
        open_api_origin="https://open.feishu.cn",
        app_id="cli_app",
        app_secret=_APP_SECRET,
    )
    return (
        LarkNativeClient(
            http_client,
            endpoint,
            tokens,
            open_api_origin="https://open.feishu.cn",
            response_max_bytes=response_max_bytes,
        ),
        tokens,
    )


def _token_response() -> httpx2.Response:
    return httpx2.Response(
        200,
        json={"code": 0, "tenant_access_token": "tenant-token", "expire": 3600},
    )


@pytest.mark.anyio
async def test_lark_token_refresh_is_async_single_flight() -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _token_response()

    endpoint = _AllowEndpoint()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http_client:
        _native, provider = _client(http_client, endpoint)
        tokens: list[str] = []

        async def retrieve() -> None:
            tokens.append(await provider.token())

        async with anyio.create_task_group() as tasks:
            for _ in range(8):
                tasks.start_soon(retrieve)

    assert tokens == ["tenant-token"] * 8
    assert len(requests) == 1
    assert json.loads(requests[0].content) == {"app_id": "cli_app", "app_secret": _APP_SECRET}
    assert _APP_SECRET not in repr(provider)


@pytest.mark.anyio
async def test_lark_token_refresh_failure_keeps_still_valid_token() -> None:
    now = [0.0]
    responses = iter(
        (
            _token_response(),
            httpx2.Response(503, json={"code": 1}),
            httpx2.Response(503, json={"code": 1}),
        )
    )
    endpoint = _AllowEndpoint()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: next(responses))) as http_client:
        provider = LarkTenantTokenProvider(
            http_client,
            endpoint,
            open_api_origin="https://open.feishu.cn",
            app_id="cli_app",
            app_secret=_APP_SECRET,
            clock=lambda: now[0],
        )
        assert await provider.token() == "tenant-token"
        now[0] = 3550.0
        assert await provider.token() == "tenant-token"
        now[0] = 3601.0
        with pytest.raises(LarkApiError, match="provider_unavailable"):
            await provider.token()


@pytest.mark.anyio
async def test_lark_forced_reply_uses_hidden_target_and_stable_provider_uuid() -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path.endswith("tenant_access_token/internal"):
            return _token_response()
        return httpx2.Response(
            200,
            json={
                "code": 0,
                "data": {"message_id": "om_reply", "root_id": "om_root", "thread_id": "omt_thread"},
            },
        )

    endpoint = _AllowEndpoint()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http_client:
        client, _tokens = _client(http_client, endpoint)
        outcome = await client.reply(
            _binding(),
            LarkForcedReplyArguments(content=LarkTextContent(text="hello")),
            effect_id="effect-1",
            request_id="req-1",
        )

    assert isinstance(outcome, LarkReplySucceeded)
    write = requests[1]
    assert write.url.path == "/open-apis/im/v1/messages/om_message/reply"
    body = json.loads(write.content)
    assert body["reply_in_thread"] is True
    assert body["msg_type"] == "text"
    assert json.loads(body["content"]) == {"text": "hello"}
    assert body["uuid"] == "47ecd4e5-7b2a-547b-a345-43634e2031ce"
    assert write.headers["authorization"] == "Bearer tenant-token"


@pytest.mark.anyio
async def test_lark_reply_argument_schema_cannot_select_or_override_destination() -> None:
    forced_schema = json.dumps(LarkForcedReplyArguments.model_json_schema(), sort_keys=True)
    auto_schema = json.dumps(LarkAutoReplyArguments.model_json_schema(), sort_keys=True)

    for forbidden in ("chat_id", "message_id", "thread_id", "tenant", "token", "ingress"):
        assert forbidden not in forced_schema
        assert forbidden not in auto_schema
    assert "placement" not in forced_schema
    assert "placement" in auto_schema

    endpoint = _AllowEndpoint()
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _request: pytest.fail("request must not be sent"))
    ) as http_client:
        client, _tokens = _client(http_client, endpoint)
        with pytest.raises(LarkApiError, match="invalid_arguments"):
            await client.reply(
                _binding(),
                LarkAutoReplyArguments(content=LarkTextContent(text="hello"), placement="main"),
                effect_id="effect-1",
                request_id="req-1",
            )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "write_response",
    [httpx2.Response(503), httpx2.Response(200, content=b"not-json")],
)
async def test_lark_reply_reports_unknown_after_ambiguous_response(
    write_response: httpx2.Response,
) -> None:
    responses = iter((_token_response(), write_response))
    endpoint = _AllowEndpoint()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: next(responses))) as http_client:
        client, _tokens = _client(http_client, endpoint)
        outcome = await client.reply(
            _binding(),
            LarkForcedReplyArguments(content=LarkTextContent(text="hello")),
            effect_id="effect-1",
            request_id="req-unknown",
        )

    assert outcome == LarkReplyOutcomeUnknown(request_id="req-unknown")


@pytest.mark.anyio
async def test_lark_reply_does_not_report_unknown_before_write_dispatch() -> None:
    endpoint = _AllowEndpoint()
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _request: httpx2.Response(503, json={"code": 1}))
    ) as http_client:
        client, _tokens = _client(http_client, endpoint)
        with pytest.raises(LarkApiError, match="provider_unavailable"):
            await client.reply(
                _binding(),
                LarkForcedReplyArguments(content=LarkTextContent(text="hello")),
                effect_id="effect-1",
                request_id="req-pre-dispatch",
            )


@pytest.mark.anyio
async def test_lark_reads_are_bounded_and_use_current_binding() -> None:
    requests: list[httpx2.Request] = []
    responses = iter(
        (
            _token_response(),
            httpx2.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [{"member_id": "ou_1", "name": "One"}],
                        "has_more": True,
                        "page_token": "member-next",
                    },
                },
            ),
            httpx2.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "message_id": "om_2",
                                "msg_type": "text",
                                "sender": {"id": "ou_1"},
                                "body": {"content": '{"text":"safe"}'},
                                "create_time": "123",
                            }
                        ],
                        "has_more": False,
                    },
                },
            ),
        )
    )

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return next(responses)

    endpoint = _AllowEndpoint()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http_client:
        client, _tokens = _client(http_client, endpoint)
        members = await client.list_members(
            _binding(),
            LarkListMembersArguments(limit=1),
        )
        messages = await client.read_messages(
            _binding(),
            LarkReadMessagesArguments(scope="discussion", limit=1, order="desc"),
        )

    assert members.items[0].name == "One"
    assert members.page_token == "member-next"
    assert messages.items[0].text == "safe"
    assert requests[1].url.path == "/open-apis/im/v1/chats/oc_chat/members"
    assert requests[2].url.params["container_id_type"] == "thread"
    assert requests[2].url.params["container_id"] == "omt_thread"
    with pytest.raises(ValidationError):
        LarkReadMessagesArguments(scope="conversation", limit=51)


@pytest.mark.anyio
async def test_lark_reads_surface_rate_limit_without_sleeping() -> None:
    responses = iter(
        (
            _token_response(),
            httpx2.Response(429, headers={"retry-after": "20"}, json={"code": 99991400}),
        )
    )
    endpoint = _AllowEndpoint()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: next(responses))) as http_client:
        client, _tokens = _client(http_client, endpoint)
        with pytest.raises(LarkApiError) as raised:
            await client.list_members(_binding(), LarkListMembersArguments())

    assert raised.value.code == "rate_limited"
    assert raised.value.retry_after_seconds == 20


@pytest.mark.anyio
async def test_lark_native_response_and_binding_representations_are_bounded() -> None:
    responses = iter((_token_response(), httpx2.Response(200, content=b"x" * 65)))
    endpoint = _AllowEndpoint()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: next(responses))) as http_client:
        client, _tokens = _client(http_client, endpoint, response_max_bytes=64)
        with pytest.raises(LarkApiError, match="response_too_large"):
            await client.list_members(_binding(), LarkListMembersArguments())

    representation = repr(_binding())
    assert "oc_chat" not in representation
    assert "om_message" not in representation
    assert "omt_thread" not in representation


@pytest.mark.anyio
async def test_lark_inspection_resolves_token_tenant_and_top_level_bot() -> None:
    seen: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/internal"):
            return _token_response()
        assert request.method == "GET"
        if request.url.path == "/open-apis/bot/v3/info":
            return httpx2.Response(
                200, json={"code": 0, "bot": {"open_id": "ou_bot", "app_name": "Helper", "activate_status": 2}}
            )
        assert request.url.path == "/open-apis/tenant/v2/tenant/query"
        return httpx2.Response(200, json={"code": 0, "data": {"tenant": {"tenant_key": "tenant1", "name": "Acme"}}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http_client:
        native, _ = _client(http_client, _AllowEndpoint())
        identity = await native.inspect_installation()
    assert (identity.app_id, identity.organization_id, identity.bot_id) == ("cli_app", "tenant1", "ou_bot")
    assert identity.enabled
    assert len(seen) == 3
    assert _APP_SECRET not in repr(identity)


@pytest.mark.anyio
@pytest.mark.parametrize("member", [True, False, None])
async def test_lark_inspection_checks_membership_separately(member: bool | None) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/internal"):
            return _token_response()
        assert request.method == "GET"
        if request.url.path.endswith("/members/is_in_chat"):
            return httpx2.Response(200, json={"code": 0, "data": {} if member is None else {"is_in_chat": member}})
        assert request.url.path == "/open-apis/im/v1/chats/oc_chat"
        return httpx2.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "name": "Engineering",
                    "chat_mode": "topic",
                    "chat_type": "private",
                    "tenant_key": "tenant1",
                    "external": False,
                },
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http_client:
        native, _ = _client(http_client, _AllowEndpoint())
        observation = await native.inspect_conversation("oc_chat")
    assert observation.audience == "private"
    assert observation.is_member is member
    assert observation.organization_id == "tenant1"


@pytest.mark.anyio
async def test_lark_discovery_propagates_page_token_without_claiming_access() -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/internal"):
            return _token_response()
        assert request.url.path == "/open-apis/im/v1/chats"
        assert request.url.params["page_token"] == "next"
        assert request.url.params["page_size"] == "2"
        return httpx2.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [{"chat_id": "oc_chat", "name": "Engineering"}],
                    "has_more": True,
                    "page_token": "after",
                },
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http_client:
        native, _ = _client(http_client, _AllowEndpoint())
        page = await native.list_conversations(limit=2, cursor="next")
    assert page.cursor == "after"
    assert page.items[0].model_dump() == {"id": "oc_chat", "name": "Engineering"}
