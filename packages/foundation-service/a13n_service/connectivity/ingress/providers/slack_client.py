"""Bounded Slack Web API client for current-context native actions."""

from __future__ import annotations

import json
from typing import Annotated, Literal

import httpx2
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError

from a13n_service.connectivity.adapters import JsonObject

from .native_http import NativeActionError, bounded_response_body, retry_after_seconds

_RESPONSE_MAX_BYTES = 1024 * 1024
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


SlackNativeActionError = NativeActionError


class SlackNativeClient:
    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        *,
        api_origin: str = "https://slack.com",
        response_max_bytes: int = _RESPONSE_MAX_BYTES,
    ) -> None:
        if api_origin.rstrip("/") != "https://slack.com":
            raise ValueError("Slack API origin must be https://slack.com")
        if not 1 <= response_max_bytes <= _RESPONSE_MAX_BYTES:
            raise ValueError("Slack response byte limit is invalid")
        self._http_client = http_client
        self._api_origin = api_origin.rstrip("/")
        self._response_max_bytes = response_max_bytes

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
        if not isinstance(channel, str) or not isinstance(message_ts, str):
            return SlackReplyOutcomeUnknown(request_id=request_id)
        root = (
            returned_root
            if isinstance(returned_root, str)
            else (binding.root_thread_ts if placement == "thread" else message_ts)
        )
        return SlackReplySucceeded(
            receipt=SlackReplyReceipt(
                channel_id=channel,
                message_ts=message_ts,
                root_thread_ts=root,
                request_id=request_id,
            )
        )

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
        items = tuple(SlackMember(user_id=value) for value in raw_members if isinstance(value, str) and value)
        cursor = _next_cursor(response)
        return SlackMemberPage(items=items, cursor=cursor, has_more=cursor is not None)

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

    async def _request(self, operation: str, payload: JsonObject, *, bot_token: str) -> JsonObject:
        if not bot_token or len(bot_token) > 4096:
            raise SlackNativeActionError("credential_unavailable")
        try:
            async with self._http_client.stream(
                "POST",
                f"{self._api_origin}/api/{operation}",
                headers={"authorization": f"Bearer {bot_token}", "content-type": "application/json"},
                json=payload,
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


def _next_cursor(value: JsonObject) -> str | None:
    metadata = value.get("response_metadata")
    cursor = metadata.get("next_cursor") if isinstance(metadata, dict) else None
    return cursor if isinstance(cursor, str) and cursor else None
