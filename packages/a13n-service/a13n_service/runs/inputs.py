"""Turning accepted message input into the content the model reads.

Each text the Service writes carries its source ID in `TextContent` metadata: an inbox entry ID for queued
messages, or the successor Run ID for a resume's accompanying input. Assets and URL content reach the model as `attachments` decides, once the run's
environments are ready to take the files it places. URL input is fetched here under the endpoint policy rather
than by the model provider, so no provider ever fetches an address the operator did not allow.
"""

import json
import posixpath
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import httpx2
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.media_types import text_charset
from a13n_harness.providers.endpoint_policy import EndpointPolicyError
from pydantic import JsonValue
from pydantic_ai.messages import TextContent, UserContent

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbound import open_http
from a13n_service.resources.assets.service import get_asset, read_asset_content
from a13n_service.runs import attachments
from a13n_service.runs.attachments import Attached, Recipient
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import AssetPart, JsonPart, MessagePayload, TextPart, UrlPart
from a13n_service.runs.tables import InboxEntryRow
from a13n_service.tenancy.authorize import Principal


@dataclass(frozen=True, slots=True)
class Offered:
    """An input detached from storage. Its source ID is an inbox entry or the resuming Run itself."""

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


async def _fetch(runtime: Runtime, url: str, *, field: str) -> tuple[str, bytes]:
    """The URL's `Content-Type` and content, fetched once. An address the policy refuses or a definitive answer
    fails the input at `field`; a network error or a transient answer is `unavailable`, so a later attempt fetches
    it again."""
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
            return response.headers.get("content-type", ""), await response.aread()
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


async def _asset(runtime: Runtime, principal: Principal, entry: Offered, asset_id: str, *, field: str) -> Attached:
    """The asset as its metadata describes it; its content is read with the run principal's access when needed."""
    asset = await get_asset(runtime.storage, principal, entry.workspace_id, asset_id)

    async def read() -> bytes:
        _, data = await read_asset_content(runtime.storage, runtime.objects, principal, entry.workspace_id, asset_id)
        return data

    return Attached(
        asset.name,
        asset.content_type,
        asset.size,
        field,
        entry.id,
        read,
        digest=asset.digest,
        identifier=asset.id,
    )


async def _url(runtime: Runtime, entry: Offered, url: str, *, field: str) -> Attached:
    """The URL's content, named by the last segment of its path."""
    content_type, data = await _fetch(runtime, url, field=field)

    async def read() -> bytes:
        return data

    return Attached(
        posixpath.basename(unquote(urlsplit(url).path)),
        content_type.partition(";")[0].strip().lower() or "application/octet-stream",
        len(data),
        field,
        entry.id,
        read,
        charset=text_charset(content_type),
        truncatable=True,
    )


async def _message(
    runtime: Runtime, principal: Principal, recipient: Recipient, environment: BoundEnvironment, entry: Offered
) -> tuple[UserContent, ...]:
    parts: list[UserContent] = []
    for index, part in enumerate(MessagePayload.model_validate(entry.payload).content):
        match part:
            case TextPart():
                parts.append(_text(entry, part.text))
            case JsonPart():
                parts.append(_text(entry, json.dumps(part.value, ensure_ascii=False)))
            case AssetPart():
                file = await _asset(runtime, principal, entry, part.asset_id, field=f"content.{index}.asset_id")
                parts.append(await attachments.attach(recipient, environment, file))
            case UrlPart():
                file = await _url(runtime, entry, part.url, field=f"content.{index}.url")
                parts.append(await attachments.attach(recipient, environment, file))
    return tuple(parts)


def _child_result(entry: Offered) -> tuple[UserContent, ...]:
    # The parent learns that a child run it spawned has ended; its details stay readable through the tools.
    return (_text(entry, "A delegated subagent run ended:\n" + json.dumps(entry.payload, ensure_ascii=False)),)


async def content(
    runtime: Runtime, principal: Principal, recipient: Recipient, environment: BoundEnvironment, entry: Offered
) -> tuple[UserContent, ...]:
    """The entry's model-visible content, its files placed in the run's primary `environment`. Assets are read with
    the run principal's access."""
    if entry.kind == "message":
        return await _message(runtime, principal, recipient, environment, entry)
    return _child_result(entry)
