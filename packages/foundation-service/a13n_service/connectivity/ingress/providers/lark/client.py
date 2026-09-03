"""Bounded Lark/Feishu OpenAPI client for current-context native actions."""

from __future__ import annotations

import json
from typing import Literal
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

import httpx2
from pydantic import JsonValue, TypeAdapter, ValidationError

from a13n_service.connectivity.http import EndpointValidator
from a13n_service.connectivity.ingress.domain import JsonObject

from .actions import (
    LarkActionBinding,
    LarkAutoReplyArguments,
    LarkForcedReplyArguments,
    LarkListMembersArguments,
    LarkMember,
    LarkMemberPage,
    LarkMessage,
    LarkMessagePage,
    LarkReadMessagesArguments,
    LarkReplyArguments,
    LarkReplyContent,
    LarkReplyOutcome,
    LarkReplyOutcomeUnknown,
    LarkReplyReceipt,
    LarkReplySucceeded,
    LarkTextContent,
)
from .api import LarkApiError, read_lark_response
from .token import LarkTenantTokenProvider

_RESPONSE_MAX_BYTES = 1024 * 1024
_JSON_OBJECT = TypeAdapter(JsonObject)


class LarkNativeClient:
    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        endpoint_validator: EndpointValidator,
        token_provider: LarkTenantTokenProvider,
        *,
        open_api_origin: str,
        response_max_bytes: int = _RESPONSE_MAX_BYTES,
    ) -> None:
        if not 1 <= response_max_bytes <= _RESPONSE_MAX_BYTES:
            raise ValueError("Lark response byte limit is invalid")
        self._http_client = http_client
        self._endpoint_validator = endpoint_validator
        self._token_provider = token_provider
        self._open_api_origin = open_api_origin.rstrip("/")
        self._response_max_bytes = response_max_bytes

    async def reply(
        self,
        binding: LarkActionBinding,
        arguments: LarkReplyArguments,
        *,
        effect_id: str,
        request_id: str,
    ) -> LarkReplyOutcome:
        placement = _reply_placement(binding, arguments)
        body = _reply_body(arguments.content, effect_id=effect_id)
        if placement == "thread":
            body["reply_in_thread"] = True
            path = f"/open-apis/im/v1/messages/{quote(binding.message_id, safe='')}/reply"
            params = None
        else:
            body["receive_id"] = binding.chat_id
            path = "/open-apis/im/v1/messages"
            params = {"receive_id_type": "chat_id"}
        origin, token = await self._authorize()
        try:
            response = await self._send(
                "POST",
                f"{origin}{path}",
                token=token,
                json_body=body,
                params=params,
            )
        except LarkApiError as error:
            if error.code not in {
                "invalid_provider_response",
                "provider_unavailable",
                "response_too_large",
            }:
                raise
            return LarkReplyOutcomeUnknown(request_id=request_id)
        data = _object(response.get("data"))
        if data is None:
            return LarkReplyOutcomeUnknown(request_id=request_id)
        message_id = data.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            return LarkReplyOutcomeUnknown(request_id=request_id)
        try:
            return LarkReplySucceeded(
                receipt=LarkReplyReceipt(
                    message_id=message_id,
                    root_id=_optional_string(data.get("root_id")),
                    thread_id=_optional_string(data.get("thread_id")),
                    request_id=request_id,
                )
            )
        except (ValueError, ValidationError):
            return LarkReplyOutcomeUnknown(request_id=request_id)

    async def list_members(
        self,
        binding: LarkActionBinding,
        arguments: LarkListMembersArguments,
    ) -> LarkMemberPage:
        params = {"member_id_type": "open_id", "page_size": str(arguments.limit)}
        if arguments.page_token is not None:
            params["page_token"] = arguments.page_token
        response = await self._request(
            "GET",
            f"/open-apis/im/v1/chats/{quote(binding.chat_id, safe='')}/members",
            params=params,
        )
        data = _required_data(response)
        raw_items = data.get("items")
        if not isinstance(raw_items, list) or len(raw_items) > arguments.limit:
            raise LarkApiError("invalid_provider_response")
        try:
            items = tuple(_member(item) for item in raw_items)
        except (ValueError, ValidationError) as error:
            raise LarkApiError("invalid_provider_response") from error
        page_token, has_more = _pagination(data)
        return LarkMemberPage(items=items, page_token=page_token, has_more=has_more)

    async def read_messages(
        self,
        binding: LarkActionBinding,
        arguments: LarkReadMessagesArguments,
    ) -> LarkMessagePage:
        params = {
            "container_id_type": "chat" if arguments.scope == "conversation" else "thread",
            "container_id": binding.chat_id if arguments.scope == "conversation" else binding.discussion_id,
            "page_size": str(arguments.limit),
            "sort_type": "ByCreateTimeAsc" if arguments.order == "asc" else "ByCreateTimeDesc",
        }
        for name, value in (
            ("start_time", arguments.start_time),
            ("end_time", arguments.end_time),
            ("page_token", arguments.page_token),
        ):
            if value is not None:
                params[name] = str(value)
        response = await self._request("GET", "/open-apis/im/v1/messages", params=params)
        data = _required_data(response)
        raw_items = data.get("items")
        if not isinstance(raw_items, list) or len(raw_items) > arguments.limit:
            raise LarkApiError("invalid_provider_response")
        try:
            items = tuple(_message(item) for item in raw_items)
        except (ValueError, ValidationError) as error:
            raise LarkApiError("invalid_provider_response") from error
        page_token, has_more = _pagination(data)
        return LarkMessagePage(items=items, page_token=page_token, has_more=has_more)

    async def _request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        json_body: JsonObject | None = None,
        params: dict[str, str] | None = None,
    ) -> JsonObject:
        origin, token = await self._authorize()
        return await self._send(
            method,
            f"{origin}{path}",
            token=token,
            json_body=json_body,
            params=params,
        )

    async def _authorize(self) -> tuple[str, str]:
        try:
            origin = await self._endpoint_validator.validate(self._open_api_origin, resolve_dns=True)
        except ValueError as error:
            raise LarkApiError("endpoint_denied") from error
        return origin, await self._token_provider.token()

    async def _send(
        self,
        method: Literal["GET", "POST"],
        url: str,
        *,
        token: str,
        json_body: JsonObject | None = None,
        params: dict[str, str] | None = None,
    ) -> JsonObject:
        try:
            async with self._http_client.stream(
                method,
                url,
                headers={"authorization": f"Bearer {token}"},
                json=json_body,
                params=params,
                follow_redirects=False,
            ) as response:
                return await read_lark_response(response, max_bytes=self._response_max_bytes)
        except LarkApiError:
            raise
        except httpx2.HTTPError as error:
            raise LarkApiError("provider_unavailable") from error


def _reply_placement(
    binding: LarkActionBinding,
    arguments: LarkReplyArguments,
) -> Literal["thread", "main"]:
    if binding.reply_mode == "auto":
        if not isinstance(arguments, LarkAutoReplyArguments):
            raise LarkApiError("invalid_arguments")
        if arguments.placement is not None:
            return arguments.placement
        return "main" if binding.chat_type == "p2p" else "thread"
    if not isinstance(arguments, LarkForcedReplyArguments):
        raise LarkApiError("invalid_arguments")
    return binding.reply_mode


def _reply_body(content: LarkReplyContent, *, effect_id: str) -> JsonObject:
    if not effect_id or len(effect_id) > 2048:
        raise LarkApiError("invalid_effect_id")
    provider_uuid = str(uuid5(NAMESPACE_URL, f"a13n:lark:{effect_id}"))
    if isinstance(content, LarkTextContent):
        payload: JsonObject = {"text": content.text}
        message_type = "text"
    else:
        payload = {
            "en_us": {
                "title": content.title or "",
                "content": [[{"tag": "text", "text": paragraph}] for paragraph in content.paragraphs],
            }
        }
        message_type = "post"
    return {
        "msg_type": message_type,
        "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        "uuid": provider_uuid,
    }


def _required_data(response: JsonObject) -> JsonObject:
    data = _object(response.get("data"))
    if data is None:
        raise LarkApiError("invalid_provider_response")
    return data


def _member(value: JsonValue) -> LarkMember:
    item = _object(value)
    if item is None:
        raise ValueError("member is not an object")
    open_id = item.get("member_id")
    name = item.get("name")
    if not isinstance(open_id, str) or not open_id or (name is not None and not isinstance(name, str)):
        raise ValueError("member is invalid")
    return LarkMember(open_id=open_id, name=name)


def _message(value: JsonValue) -> LarkMessage:
    item = _object(value)
    if item is None:
        raise ValueError("message is not an object")
    message_id = item.get("message_id")
    message_type = item.get("msg_type")
    sender = _object(item.get("sender"))
    sender_id = sender.get("id") if sender is not None else None
    content = _object(item.get("body"))
    raw_content = content.get("content") if content is not None else None
    if not isinstance(message_id, str) or not isinstance(message_type, str):
        raise ValueError("message identity is invalid")
    text = _project_text(message_type, raw_content)
    return LarkMessage(
        message_id=message_id,
        message_type=message_type,
        sender_open_id=sender_id if isinstance(sender_id, str) else None,
        text=text,
        create_time=_optional_string(item.get("create_time")),
    )


def _project_text(message_type: str, raw_content: JsonValue | None) -> str | None:
    if message_type != "text" or not isinstance(raw_content, str) or len(raw_content) > 512 * 1024:
        return None
    try:
        content = _JSON_OBJECT.validate_python(json.loads(raw_content))
    except (json.JSONDecodeError, ValidationError):
        return None
    text = content.get("text")
    return text if isinstance(text, str) and len(text) <= 40_000 else None


def _pagination(data: JsonObject) -> tuple[str | None, bool]:
    page_token = _optional_string(data.get("page_token"))
    has_more = data.get("has_more")
    if not isinstance(has_more, bool):
        raise LarkApiError("invalid_provider_response")
    if has_more and page_token is None:
        raise LarkApiError("invalid_provider_response")
    return page_token, has_more


def _object(value: JsonValue | None) -> JsonObject | None:
    return value if isinstance(value, dict) else None


def _optional_string(value: JsonValue | None) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("invalid provider string")
    return value
