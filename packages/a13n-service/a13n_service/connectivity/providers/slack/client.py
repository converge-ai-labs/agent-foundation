"""Bounded Slack Web API client for current-context native actions."""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from time import monotonic
from typing import Annotated, Literal

import httpx2
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError

from a13n_service.connectivity.adapters import JsonObject
from a13n_service.connectivity.http import ConnectivityHttpError, bounded_response_body, retry_after_seconds
from a13n_service.connectivity.inspection import ConversationInfo, ConversationPage, InstallationInfo

from . import inspection

_RESPONSE_MAX_BYTES = 1024 * 1024
_MEMBER_CACHE_MAX_ENTRIES = 512
_MEMBER_CACHE_TTL_SECONDS = 300.0
_JSON_OBJECT = TypeAdapter(JsonObject)
BoundedText = Annotated[str, StringConstraints(min_length=1, max_length=40_000)]
BoundedCursor = Annotated[str, StringConstraints(min_length=1, max_length=2048)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SlackActionBinding(_StrictModel):
    channel_id: str = Field(min_length=1, max_length=128, repr=False)
    root_thread_ts: str = Field(min_length=1, max_length=128, repr=False)
    conversation_kind: Literal["channel", "group", "im", "mpim"]
    reply_mode: Literal["auto", "thread", "main"]


class SlackForcedReplyArguments(_StrictModel):
    text: BoundedText = Field(repr=False)


class SlackAutoReplyArguments(_StrictModel):
    text: BoundedText = Field(repr=False)
    placement: Literal["thread", "main"] | None = None


type SlackReplyArguments = SlackForcedReplyArguments | SlackAutoReplyArguments


class SlackReplyReceipt(_StrictModel):
    channel_id: str = Field(min_length=1, max_length=128, repr=False)
    message_ts: str = Field(min_length=1, max_length=128, repr=False)
    root_thread_ts: str = Field(min_length=1, max_length=128, repr=False)
    request_id: str = Field(min_length=1, max_length=128)


class SlackReplySucceeded(_StrictModel):
    kind: Literal["succeeded"] = "succeeded"
    receipt: SlackReplyReceipt


class SlackReplyOutcomeUnknown(_StrictModel):
    kind: Literal["outcome_unknown"] = "outcome_unknown"
    request_id: str = Field(min_length=1, max_length=128)


type SlackReplyOutcome = SlackReplySucceeded | SlackReplyOutcomeUnknown


class SlackListMembersArguments(_StrictModel):
    limit: int = Field(default=100, ge=1, le=100)
    cursor: BoundedCursor | None = None


class SlackMember(_StrictModel):
    user_id: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=256)
    is_bot: bool | None = None
    deleted: bool | None = None


class SlackMemberPage(_StrictModel):
    items: tuple[SlackMember, ...]
    cursor: str | None = Field(default=None, max_length=2048)
    has_more: bool


class SlackReadMessagesArguments(_StrictModel):
    scope: Literal["conversation", "discussion"]
    limit: int = Field(default=15, ge=1, le=15)
    cursor: BoundedCursor | None = None


class SlackMessage(_StrictModel):
    message_ts: str = Field(min_length=1, max_length=128)
    user_id: str | None = Field(default=None, max_length=128)
    text: str = Field(max_length=40_000, repr=False)
    root_thread_ts: str | None = Field(default=None, max_length=128)


class SlackMessagePage(_StrictModel):
    items: tuple[SlackMessage, ...]
    cursor: str | None = Field(default=None, max_length=2048)
    has_more: bool


SlackNativeActionError = ConnectivityHttpError


@dataclass(frozen=True, slots=True)
class _CachedMember:
    member: SlackMember
    expires_at: float


class SlackNativeClient:
    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        *,
        api_origin: str = "https://slack.com",
        response_max_bytes: int = _RESPONSE_MAX_BYTES,
        member_cache_max_entries: int = _MEMBER_CACHE_MAX_ENTRIES,
        member_cache_ttl_seconds: float = _MEMBER_CACHE_TTL_SECONDS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if api_origin.rstrip("/") != "https://slack.com":
            raise ValueError("Slack API origin must be https://slack.com")
        if not 1 <= response_max_bytes <= _RESPONSE_MAX_BYTES:
            raise ValueError("Slack response byte limit is invalid")
        if not 1 <= member_cache_max_entries <= _MEMBER_CACHE_MAX_ENTRIES:
            raise ValueError("Slack member cache bound is invalid")
        if not 1 <= member_cache_ttl_seconds <= _MEMBER_CACHE_TTL_SECONDS:
            raise ValueError("Slack member cache TTL is invalid")
        self._http_client = http_client
        self._api_origin = api_origin.rstrip("/")
        self._response_max_bytes = response_max_bytes
        self._member_cache_max_entries = member_cache_max_entries
        self._member_cache_ttl_seconds = member_cache_ttl_seconds
        self._clock = clock
        self._member_cache: OrderedDict[tuple[bytes, str], _CachedMember] = OrderedDict()

    async def reply(
        self,
        binding: SlackActionBinding,
        arguments: SlackReplyArguments,
        *,
        bot_token: str,
        request_id: str,
    ) -> SlackReplyOutcome:
        placement = _reply_placement(binding, arguments)
        payload: JsonObject = {"channel": binding.channel_id, "text": arguments.text}
        if placement == "thread":
            payload["thread_ts"] = binding.root_thread_ts
        return await self._post_message(payload, bot_token=bot_token, request_id=request_id)

    async def send_message(self, channel_id: str, text: str, *, bot_token: str, request_id: str) -> SlackReplyOutcome:
        return await self._post_message(
            {"channel": channel_id, "text": text}, bot_token=bot_token, request_id=request_id
        )

    async def _post_message(self, payload: JsonObject, *, bot_token: str, request_id: str) -> SlackReplyOutcome:
        try:
            response = await self._request("chat.postMessage", payload, bot_token=bot_token)
        except SlackNativeActionError as error:
            if error.code not in {
                "invalid_provider_response",
                "provider_unavailable",
                "response_too_large",
            }:
                raise
            return SlackReplyOutcomeUnknown(request_id=request_id)
        channel = response.get("channel")
        message_ts = response.get("ts")
        message = response.get("message")
        returned_root = message.get("thread_ts") if isinstance(message, dict) else None
        if not isinstance(channel, str) or channel != payload["channel"] or not isinstance(message_ts, str):
            return SlackReplyOutcomeUnknown(request_id=request_id)
        expected_root = payload.get("thread_ts", message_ts)
        if returned_root is not None and returned_root != expected_root:
            return SlackReplyOutcomeUnknown(request_id=request_id)
        root = (
            returned_root
            if isinstance(returned_root, str)
            else (str(payload["thread_ts"]) if "thread_ts" in payload else message_ts)
        )
        try:
            return SlackReplySucceeded(
                receipt=SlackReplyReceipt(
                    channel_id=channel,
                    message_ts=message_ts,
                    root_thread_ts=root,
                    request_id=request_id,
                )
            )
        except ValidationError:
            return SlackReplyOutcomeUnknown(request_id=request_id)

    async def list_members(
        self,
        binding: SlackActionBinding,
        arguments: SlackListMembersArguments,
        *,
        bot_token: str,
    ) -> SlackMemberPage:
        payload: JsonObject = {"channel": binding.channel_id, "limit": arguments.limit}
        if arguments.cursor is not None:
            payload["cursor"] = arguments.cursor
        response = await self._request("conversations.members", payload, bot_token=bot_token)
        raw_members = response.get("members")
        if not isinstance(raw_members, list) or len(raw_members) > arguments.limit:
            raise SlackNativeActionError("invalid_provider_response")
        member_ids: list[str] = []
        for value in raw_members:
            if not isinstance(value, str) or not value:
                raise SlackNativeActionError("invalid_provider_response")
            member_ids.append(value)
        token_scope = sha256(bot_token.encode()).digest()
        items = tuple([await self._member(value, token_scope=token_scope, bot_token=bot_token) for value in member_ids])
        cursor = _next_cursor(response)
        return SlackMemberPage(items=items, cursor=cursor, has_more=cursor is not None)

    async def _member(self, user_id: str, *, token_scope: bytes, bot_token: str) -> SlackMember:
        key = (token_scope, user_id)
        now = self._clock()
        cached = self._member_cache.get(key)
        if cached is not None and now < cached.expires_at:
            self._member_cache.move_to_end(key)
            return cached.member
        response = await self._request("users.info", {"user": user_id}, bot_token=bot_token)
        member = _member(response.get("user"), expected_user_id=user_id)
        self._member_cache[key] = _CachedMember(
            member=member,
            expires_at=now + self._member_cache_ttl_seconds,
        )
        self._member_cache.move_to_end(key)
        while len(self._member_cache) > self._member_cache_max_entries:
            self._member_cache.popitem(last=False)
        return member

    async def read_messages(
        self,
        binding: SlackActionBinding,
        arguments: SlackReadMessagesArguments,
        *,
        bot_token: str,
    ) -> SlackMessagePage:
        operation = "conversations.history" if arguments.scope == "conversation" else "conversations.replies"
        payload: JsonObject = {"channel": binding.channel_id, "limit": arguments.limit}
        if arguments.scope == "discussion":
            payload["ts"] = binding.root_thread_ts
        if arguments.cursor is not None:
            payload["cursor"] = arguments.cursor
        response = await self._request(operation, payload, bot_token=bot_token)
        raw_messages = response.get("messages")
        if not isinstance(raw_messages, list) or len(raw_messages) > arguments.limit:
            raise SlackNativeActionError("invalid_provider_response")
        try:
            items = tuple(_message(value) for value in raw_messages)
        except (ValidationError, ValueError) as error:
            raise SlackNativeActionError("invalid_provider_response") from error
        cursor = _next_cursor(response)
        return SlackMessagePage(items=items, cursor=cursor, has_more=cursor is not None)

    async def inspect_installation(self, *, bot_token: str) -> InstallationInfo:
        auth = await self._request("auth.test", {}, bot_token=bot_token)
        bot_id = auth.get("bot_id")
        if not isinstance(bot_id, str) or not 1 <= len(bot_id) <= 256:
            raise SlackNativeActionError("invalid_provider_response")
        bot = await self._request("bots.info", {"bot": bot_id}, bot_token=bot_token, method="GET")
        return inspection.installation(auth, bot)

    async def inspect_conversation(self, channel_id: str, *, bot_token: str) -> ConversationInfo:
        if not 1 <= len(channel_id) <= 512:
            raise SlackNativeActionError("invalid_arguments")
        response = await self._request("conversations.info", {"channel": channel_id}, bot_token=bot_token, method="GET")
        return inspection.conversation(response, expected_id=channel_id)

    async def list_conversations(
        self, *, bot_token: str, limit: int = 100, cursor: str | None = None
    ) -> ConversationPage:
        if not 1 <= limit <= 100 or (cursor is not None and not 1 <= len(cursor) <= 2048):
            raise SlackNativeActionError("invalid_arguments")
        payload: JsonObject = {
            "limit": str(limit),
            "types": "public_channel,private_channel",
            "exclude_archived": "true",
        }
        if cursor is not None:
            payload["cursor"] = cursor
        response = await self._request("users.conversations", payload, bot_token=bot_token, method="GET")
        return inspection.conversations(response, limit=limit)

    async def _request(
        self, operation: str, payload: JsonObject, *, bot_token: str, method: Literal["GET", "POST"] = "POST"
    ) -> JsonObject:
        if not bot_token or len(bot_token) > 4096:
            raise SlackNativeActionError("credential_unavailable")
        try:
            async with self._http_client.stream(
                method,
                f"{self._api_origin}/api/{operation}",
                headers={"authorization": f"Bearer {bot_token}", "content-type": "application/json"},
                json=payload if method == "POST" else None,
                params={key: str(value) for key, value in payload.items()} if method == "GET" else None,
                follow_redirects=False,
            ) as response:
                retry_after = retry_after_seconds(response.headers.get("retry-after"))
                body = await bounded_response_body(response, max_bytes=self._response_max_bytes)
                if response.status_code == 429:
                    raise SlackNativeActionError("rate_limited", retry_after_seconds=retry_after)
                if response.status_code >= 500:
                    raise SlackNativeActionError("provider_unavailable")
                if response.status_code != 200:
                    raise SlackNativeActionError("provider_rejected")
        except SlackNativeActionError:
            raise
        except httpx2.HTTPError as error:
            raise SlackNativeActionError("provider_unavailable") from error
        try:
            value = _JSON_OBJECT.validate_python(json.loads(body))
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as error:
            raise SlackNativeActionError("invalid_provider_response") from error
        if value.get("ok") is not True:
            error_code = value.get("error")
            if error_code == "ratelimited":
                raise SlackNativeActionError("rate_limited", retry_after_seconds=retry_after)
            raise SlackNativeActionError("provider_rejected")
        return value


def _reply_placement(binding: SlackActionBinding, arguments: SlackReplyArguments) -> Literal["thread", "main"]:
    if binding.reply_mode == "auto":
        if not isinstance(arguments, SlackAutoReplyArguments):
            raise SlackNativeActionError("invalid_arguments")
        if arguments.placement is not None:
            return arguments.placement
        return "main" if binding.conversation_kind == "im" else "thread"
    if not isinstance(arguments, SlackForcedReplyArguments):
        raise SlackNativeActionError("invalid_arguments")
    return binding.reply_mode


def _message(value: object) -> SlackMessage:
    if not isinstance(value, dict):
        raise ValueError("message is not an object")
    message_ts = value.get("ts")
    user_id = value.get("user")
    text = value.get("text", "")
    root_thread_ts = value.get("thread_ts")
    if not isinstance(message_ts, str) or not message_ts:
        raise ValueError("message timestamp is invalid")
    if user_id is not None and not isinstance(user_id, str):
        raise ValueError("message user is invalid")
    if not isinstance(text, str):
        raise ValueError("message text is invalid")
    if root_thread_ts is not None and not isinstance(root_thread_ts, str):
        raise ValueError("message thread is invalid")
    return SlackMessage(
        message_ts=message_ts,
        user_id=user_id,
        text=text,
        root_thread_ts=root_thread_ts,
    )


def _member(value: object, *, expected_user_id: str) -> SlackMember:
    if not isinstance(value, dict) or value.get("id") != expected_user_id:
        raise SlackNativeActionError("invalid_provider_response")
    profile = value.get("profile")
    if profile is not None and not isinstance(profile, dict):
        raise SlackNativeActionError("invalid_provider_response")
    profile = profile or {}
    display_name = profile.get("display_name") or profile.get("real_name") or value.get("name")
    if display_name is not None and not isinstance(display_name, str):
        raise SlackNativeActionError("invalid_provider_response")
    is_bot = value.get("is_bot")
    deleted = value.get("deleted")
    if is_bot is not None and not isinstance(is_bot, bool):
        raise SlackNativeActionError("invalid_provider_response")
    if deleted is not None and not isinstance(deleted, bool):
        raise SlackNativeActionError("invalid_provider_response")
    try:
        return SlackMember(
            user_id=expected_user_id,
            display_name=display_name or None,
            is_bot=is_bot,
            deleted=deleted,
        )
    except ValidationError as error:
        raise SlackNativeActionError("invalid_provider_response") from error


def _next_cursor(value: JsonObject) -> str | None:
    metadata = value.get("response_metadata")
    cursor = metadata.get("next_cursor") if isinstance(metadata, dict) else None
    return cursor if isinstance(cursor, str) and cursor else None
