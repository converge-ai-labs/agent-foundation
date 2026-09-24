"""Mem0 record stores over its REST APIs: the hosted Platform and the self-hosted server.

A memory's namespace is a mem0 `user_id`. Records are stored verbatim (`infer`
false), and every write is confirmed by reading the record back.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, ClassVar
from urllib.parse import quote

import httpx2
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, field_validator

from a13n_harness._urls import require_http_url
from a13n_harness.http import ProviderHttpError, bounded_response_body

from ..authentication import Authentication, CredentialMode
from ..endpoint_policy import EndpointPolicyError
from .contracts import MemoryRecord, MemoryStoreError, RecordPage, RecordStore, validate_record_text
from .definition import MemoryProviderDefinition

# The longest record text a mem0 store accepts.
_TEXT_CHARS = 8000
_RESPONSE_BYTES = 8 * 1024 * 1024
_PLATFORM_PAGE_SIZE = 200
# The self-hosted server lists at most this many records and does not page.
_OSS_LIST_LIMIT = 1000
_UNCONFIRMED = "mem0 did not confirm the write; it may or may not have happened."
# A status that means the record, or the namespace's records, do not exist.
_GONE = frozenset({404})
_MISSING = object()

type _BaseUrl = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]


class _Mem0Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: _BaseUrl

    @field_validator("base_url")
    @classmethod
    def _plain_base_url(cls, value: str) -> str:
        parsed = require_http_url(value)
        if parsed.query or parsed.fragment:
            raise ValueError("A base URL has no query or fragment.")
        return value.rstrip("/")


class Mem0PlatformConfiguration(_Mem0Configuration):
    base_url: _BaseUrl = Field(default="https://api.mem0.ai", title="Base URL")


class Mem0OSSConfiguration(_Mem0Configuration):
    base_url: _BaseUrl = Field(title="Base URL", description="Address of the mem0 REST server")


class Mem0Credential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key: SecretStr = Field(title="API Key", min_length=1, max_length=4096)


class _Refused(Exception):
    """mem0 answered 4xx, so it did not act on the request."""


class _Failed(Exception):
    """mem0 could not be reached, failed, or answered something unreadable; a write's outcome is unknown."""


class _Mem0Store:
    """What both mem0 APIs share: the namespace filter, verbatim adds, and confirmation by readback."""

    _ADD: ClassVar[str]
    _SEARCH: ClassVar[str]
    _RECORD: ClassVar[str]
    _PURGE: ClassVar[str]

    def __init__(self, client: httpx2.AsyncClient, base_url: str, headers: Mapping[str, str], namespace: str) -> None:
        if not 1 <= len(namespace) <= 256 or "*" in namespace or " " in namespace or not namespace.isprintable():
            raise ValueError("A mem0 namespace has 1 to 256 printable characters and no spaces or '*'.")
        self._client = client
        self._base_url = base_url
        self._headers = dict(headers)
        self._namespace = namespace

    async def search(self, query: str, *, limit: int) -> tuple[MemoryRecord, ...]:
        request = self._request("POST", self._SEARCH, json={"query": query, "filters": self._filter, "top_k": limit})
        return self._own(_results(await self._read(request))[:limit])

    async def add(self, text: str) -> MemoryRecord:
        validate_record_text(text, max_chars=_TEXT_CHARS)
        body = await self._write(
            self._request(
                "POST",
                self._ADD,
                json={"messages": [{"role": "user", "content": text}], "user_id": self._namespace, "infer": False},
            )
        )
        return await self._confirm(_added_id(body), text)

    async def update(self, record_id: str, text: str) -> MemoryRecord:
        validate_record_text(text, max_chars=_TEXT_CHARS)
        await self._require(record_id)
        if (
            await self._write(self._request("PUT", self._path(record_id), json={"text": text}), missing=_GONE)
            is _MISSING
        ):
            raise _not_found(record_id)
        return await self._confirm(record_id, text)

    async def delete(self, record_id: str) -> None:
        await self._require(record_id)
        # A record another writer deleted meanwhile is deleted all the same.
        await self._write(self._request("DELETE", self._path(record_id)), missing=_GONE)
        try:
            remaining = await self._get(record_id)
        except MemoryStoreError:
            raise MemoryStoreError("write_unconfirmed", _UNCONFIRMED) from None
        if remaining is not None:
            raise MemoryStoreError("write_unconfirmed", _UNCONFIRMED)

    async def purge(self) -> None:
        await self._read(self._request("DELETE", self._PURGE, params={"user_id": self._namespace}), missing=_GONE)

    @property
    def _filter(self) -> dict[str, str]:
        return {"user_id": self._namespace}

    def _path(self, record_id: str) -> str:
        return self._RECORD.format(quote(record_id, safe=""))

    def _request(
        self, method: str, path: str, *, json: object = None, params: Mapping[str, str | int] | None = None
    ) -> httpx2.Request:
        return self._client.build_request(
            method, f"{self._base_url}{path}", headers=self._headers, json=json, params=params
        )

    def _own(self, raws: Sequence[Mapping[str, object]]) -> tuple[MemoryRecord, ...]:
        """The records of this namespace. The filter already excludes others; a stray one is dropped."""
        return tuple(record for record, owner in map(_record, raws) if owner in (None, self._namespace))

    async def _get(self, record_id: str) -> MemoryRecord | None:
        """The record when it exists in this namespace. mem0 IDs are global, so another namespace's is absent."""
        # The Platform answers 400 for a malformed ID; the self-hosted server answers null for an unknown one.
        body = await self._read(self._request("GET", self._path(record_id)), missing=frozenset({400, 404}))
        if body is _MISSING or body is None:
            return None
        if not isinstance(body, Mapping):
            raise _unavailable()
        record, owner = _record(body)
        return record if owner == self._namespace and record.id == record_id else None

    async def _require(self, record_id: str) -> None:
        if await self._get(record_id) is None:
            raise _not_found(record_id)

    async def _confirm(self, record_id: str, text: str) -> MemoryRecord:
        try:
            record = await self._get(record_id)
        except MemoryStoreError:
            raise MemoryStoreError("write_unconfirmed", _UNCONFIRMED) from None
        if record is None or record.text != text:
            raise MemoryStoreError("write_unconfirmed", _UNCONFIRMED)
        return record

    async def _read(self, request: httpx2.Request, *, missing: frozenset[int] = frozenset()) -> object:
        try:
            return await self._send(request, missing)
        except (_Refused, _Failed):
            raise _unavailable() from None

    async def _write(self, request: httpx2.Request, *, missing: frozenset[int] = frozenset()) -> object:
        try:
            return await self._send(request, missing)
        except _Refused:
            raise MemoryStoreError("unavailable", "mem0 refused the write; nothing changed.") from None
        except _Failed:
            raise MemoryStoreError("write_unconfirmed", _UNCONFIRMED) from None

    async def _send(self, request: httpx2.Request, missing: frozenset[int]) -> object:
        """The JSON body of a 2xx answer, or `_MISSING` for a status in `missing`."""
        try:
            response = await self._client.send(request, stream=True)
            try:
                content = await bounded_response_body(response, max_bytes=_RESPONSE_BYTES)
            finally:
                await response.aclose()
        except (httpx2.HTTPError, ProviderHttpError, EndpointPolicyError) as error:
            raise _Failed from error
        status = response.status_code
        if status in missing:
            return _MISSING
        if 400 <= status < 500:
            raise _Refused
        if not 200 <= status < 300:
            raise _Failed
        try:
            return json.loads(content) if content else None
        except ValueError as error:
            raise _Failed from error


class _PlatformStore(_Mem0Store):
    """The hosted Platform API: https://docs.mem0.ai/api-reference."""

    _ADD = "/v3/memories/add/"
    _SEARCH = "/v3/memories/search/"
    _RECORD = "/v1/memories/{}/"
    _PURGE = "/v1/memories/"

    async def list(self, *, limit: int, cursor: str | None = None) -> RecordPage:
        # The cursor is an offset, so a caller that changes `limit` between pages neither skips nor repeats.
        size = min(limit, _PLATFORM_PAGE_SIZE)
        page, skip = divmod(_offset(cursor), size)
        body = await self._read(
            self._request(
                "POST", "/v3/memories/", json={"filters": self._filter}, params={"page": page + 1, "page_size": size}
            )
        )
        more = isinstance(body, Mapping) and body.get("next") is not None
        return RecordPage(self._own(_results(body)[skip:]), str((page + 1) * size) if more else None)


class _OSSStore(_Mem0Store):
    """The self-hosted REST server: https://docs.mem0.ai/open-source/features/rest-api."""

    _ADD = "/memories"
    _SEARCH = "/search"
    _RECORD = "/memories/{}"
    _PURGE = "/memories"

    async def list(self, *, limit: int, cursor: str | None = None) -> RecordPage:
        start = _offset(cursor)
        end = start + limit
        top_k = min(end + 1, _OSS_LIST_LIMIT)
        body = await self._read(self._request("GET", "/memories", params={"user_id": self._namespace, "top_k": top_k}))
        raws = _results(body)
        return RecordPage(self._own(raws[start:end]), str(end) if len(raws) > end else None)


def _results(body: object) -> list[Mapping[str, object]]:
    results = body.get("results") if isinstance(body, Mapping) else None
    if not isinstance(results, list) or not all(isinstance(item, Mapping) for item in results):
        raise _unavailable()
    return results


def _record(raw: Mapping[str, object]) -> tuple[MemoryRecord, object]:
    """A record and the `user_id` it belongs to."""
    record_id, text, score = raw.get("id"), raw.get("memory"), raw.get("score")
    if not isinstance(record_id, str) or not isinstance(text, str):
        raise _unavailable()
    return MemoryRecord(
        id=record_id,
        text=text,
        score=float(score) if isinstance(score, int | float) and not isinstance(score, bool) else None,
        updated_at=_timestamp(raw.get("updated_at") or raw.get("created_at")),
    ), raw.get("user_id")


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _added_id(body: object) -> str:
    """The ID of the one record an add created."""
    results = body.get("results") if isinstance(body, Mapping) else None
    if isinstance(results, list) and len(results) == 1 and isinstance(item := results[0], Mapping):
        record_id = item.get("id")
        if item.get("event") == "ADD" and isinstance(record_id, str) and record_id:
            return record_id
    raise MemoryStoreError("write_unconfirmed", _UNCONFIRMED)


def _offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not cursor.isascii() or not cursor.isdecimal() or len(cursor) > 9:
        raise MemoryStoreError("invalid_cursor", "Use the next_cursor of a previous page.")
    return int(cursor)


def _not_found(record_id: str) -> MemoryStoreError:
    return MemoryStoreError("record_not_found", f"No record {record_id!r} in this memory.")


def _unavailable() -> MemoryStoreError:
    return MemoryStoreError("unavailable", "mem0 is unavailable or answered unexpectedly.")


@asynccontextmanager
async def _open_platform(
    configuration: Mem0PlatformConfiguration,
    credential: Mem0Credential | None,
    namespace: str,
    http: httpx2.AsyncClient,
) -> AsyncIterator[RecordStore]:
    if credential is None:
        raise ValueError("Mem0 Platform requires an API key.")
    headers = {"Authorization": f"Token {credential.api_key.get_secret_value()}"}
    yield _PlatformStore(http, configuration.base_url, headers, namespace)


@asynccontextmanager
async def _open_oss(
    configuration: Mem0OSSConfiguration, credential: Mem0Credential | None, namespace: str, http: httpx2.AsyncClient
) -> AsyncIterator[RecordStore]:
    headers = {} if credential is None else {"X-API-Key": credential.api_key.get_secret_value()}
    yield _OSSStore(http, configuration.base_url, headers, namespace)


MEM0_PLATFORM = MemoryProviderDefinition(
    type="mem0_platform",
    display_name="Mem0 Platform",
    configuration_model=Mem0PlatformConfiguration,
    credential_model=Mem0Credential,
    open_store=_open_platform,
    setup_url="https://app.mem0.ai/dashboard/api-keys",
    setup_label="Mem0 dashboard",
)
MEM0_OSS = MemoryProviderDefinition(
    type="mem0_oss",
    display_name="Mem0 (self-hosted)",
    configuration_model=Mem0OSSConfiguration,
    credential_model=Mem0Credential,
    authentication=Authentication(mode=CredentialMode.optional),
    open_store=_open_oss,
    setup_url="https://docs.mem0.ai/open-source/features/rest-api",
    setup_label="Mem0 REST server guide",
)

__all__ = [
    "MEM0_OSS",
    "MEM0_PLATFORM",
    "Mem0Credential",
    "Mem0OSSConfiguration",
    "Mem0PlatformConfiguration",
]
