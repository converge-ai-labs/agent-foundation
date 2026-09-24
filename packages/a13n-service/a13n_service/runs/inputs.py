"""Turning inbox entries into the content the model reads.

Each part carries its entry ID in `TextContent` metadata, so display items of the user's input name the entry
they came from. URL input is fetched here under the endpoint policy rather than by the model provider, so no
provider ever fetches an address the operator did not allow.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

import httpx2
from a13n_harness import DeferredToolResume
from a13n_harness.providers.endpoint_policy import EndpointPolicyError
from pydantic import JsonValue
from pydantic_ai.messages import BinaryContent, TextContent, UserContent

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbound import open_http
from a13n_service.resources.assets.service import read_asset_content
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import AssetPart, JsonPart, MessagePayload, TextPart, UrlPart
from a13n_service.runs.tables import InboxEntryRow
from a13n_service.tenancy.authorize import Principal


@dataclass(frozen=True, slots=True)
class MessageInput:
    content: tuple[UserContent, ...]
    kind: Literal["message"] = field(default="message", init=False)


@dataclass(frozen=True, slots=True)
class DeferredInput:
    resume: DeferredToolResume
    kind: Literal["deferred"] = field(default="deferred", init=False)


type AcceptedInput = MessageInput | DeferredInput


@dataclass(frozen=True, slots=True)
class Offered:
    """An assigned entry as the worker offers it, detached from its row."""

    id: str
    kind: str
    workspace_id: str
    payload: dict[str, JsonValue]

    @classmethod
    def of(cls, entry: InboxEntryRow) -> "Offered":
        return cls(entry.id, entry.kind, entry.workspace_id, entry.payload)


def _text(entry: Offered, text: str) -> TextContent:
    return TextContent(text, metadata={"source_id": entry.id})


# Answers that a later fetch may not repeat; any other status but 200 refuses the input.
_TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


async def _fetch(runtime: Runtime, url: str, *, field: str) -> BinaryContent:
    """The URL's content, fetched once. An address the policy refuses or a definitive answer fails the input at
    `field`; a network error or a transient answer is `unavailable`, so a later attempt fetches it again."""
    providers = runtime.settings.providers
    try:
        async with open_http(
            runtime.endpoint_policy, timeout=providers.operation_seconds, max_bytes=providers.response_bytes
        ) as client:
            response = await client.get(url)
            if response.status_code in _TRANSIENT_STATUSES:
                raise _unreachable({"status": response.status_code})
            if response.status_code != 200:
                raise _unfetchable(field, "http_status", status=response.status_code)
            media_type = response.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
            return BinaryContent(await response.aread(), media_type=media_type or "application/octet-stream")
    except (httpx2.TimeoutException, httpx2.NetworkError) as error:
        raise _unreachable({"reason": type(error).__name__}) from None
    except EndpointPolicyError:
        raise _unfetchable(field, "endpoint_denied") from None
    except httpx2.HTTPError:
        raise _unfetchable(field, "unreadable") from None


def _unfetchable(field: str, reason: str, **details: JsonValue) -> ServiceError:
    return ServiceError(
        "invalid_argument", "URL input could not be fetched", {"field": field, "reason": reason, **details}
    )


def _unreachable(details: dict[str, JsonValue]) -> ServiceError:
    return ServiceError("unavailable", "URL input could not be fetched", {"dependency": "url", **details})


async def _message(runtime: Runtime, principal: Principal, entry: Offered) -> list[UserContent]:
    content: list[UserContent] = []
    for index, part in enumerate(MessagePayload.model_validate(entry.payload).content):
        match part:
            case TextPart():
                content.append(_text(entry, part.text))
            case JsonPart():
                content.append(_text(entry, json.dumps(part.value, ensure_ascii=False)))
            case AssetPart():
                asset, data = await read_asset_content(
                    runtime.storage, runtime.objects, principal, entry.workspace_id, part.asset_id
                )
                content.append(BinaryContent(data, media_type=asset.content_type, identifier=asset.id))
            case UrlPart():
                content.append(await _fetch(runtime, part.url, field=f"content.{index}.url"))
    return content


def _child_result(entry: Offered) -> list[UserContent]:
    # The parent learns that a child run it spawned has ended; its details stay readable through the tools.
    return [_text(entry, "A delegated subagent run ended:\n" + json.dumps(entry.payload, ensure_ascii=False))]


async def content(runtime: Runtime, principal: Principal, entries: Sequence[Offered]) -> list[UserContent]:
    """The model-visible content of entries, in order. Assets are read with the run principal's access."""
    result: list[UserContent] = []
    for entry in entries:
        result.extend(await _message(runtime, principal, entry) if entry.kind == "message" else _child_result(entry))
    return result
