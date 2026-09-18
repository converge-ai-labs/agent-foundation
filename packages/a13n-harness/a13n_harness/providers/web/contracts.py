"""Bounded Web operations and policy ports, independent of Agent runs."""

from __future__ import annotations

from collections.abc import AsyncIterable, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from a13n_harness._urls import require_audience_safe_url as _require_audience_safe_url
from a13n_harness._urls import require_http_url as _require_http_url
from a13n_harness.providers.usage import ProviderUsage
from a13n_harness.providers.web.domains import DomainRestrictions

_MAX_RESPONSE_HEADERS = 256
_MAX_RESPONSE_HEADER_BYTES = 256 * 1024
_MAX_RESPONSE_URL_BYTES = 16 * 1024


def _validate_headers(headers: Mapping[str, str], *, max_count: int, max_bytes: int) -> None:
    if len(headers) > max_count:
        raise ValueError("web response has too many headers")
    total = 0
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str) or "\x00" in key or "\x00" in value:
            raise TypeError("web response headers must be NUL-free strings")
        total += len(key.encode("utf-8")) + len(value.encode("utf-8"))
        if total > max_bytes:
            raise ValueError("web response headers are too large")


type WebPurpose = Literal["fetch", "download", "scrape"]
type WebMethod = Literal["GET", "HEAD"]


class WebRequest(BaseModel):
    """Finite transport request; clients must reapply policy to every redirect hop."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    url: str = Field(min_length=1, max_length=16 * 1024)
    method: WebMethod = "GET"
    purpose: WebPurpose
    deadline_seconds: float = Field(gt=0, allow_inf_nan=False)
    max_redirects: int = Field(ge=0, le=64)
    max_response_bytes: int = Field(gt=0)
    max_header_count: int = Field(gt=0, le=_MAX_RESPONSE_HEADERS)
    max_header_bytes: int = Field(gt=0, le=_MAX_RESPONSE_HEADER_BYTES)
    max_stream_chunk_bytes: int = Field(gt=0, le=1024 * 1024)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        _require_http_url(value)
        return value


@dataclass(slots=True)
class WebResponse:
    """One streamed response whose body and connection are owned by the caller."""

    status_code: int
    final_url: str
    canonical_url: str
    headers: Mapping[str, str]
    body: AsyncIterable[bytes]
    reason: str | None = None
    redirect_count: int = 0
    usage: tuple[ProviderUsage, ...] = ()
    _close: Callable[[], Awaitable[None]] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not 100 <= self.status_code <= 599:
            raise ValueError("web response status code is invalid")
        _require_http_url(self.final_url)
        _require_audience_safe_url(self.canonical_url)
        if max(len(self.final_url.encode("utf-8")), len(self.canonical_url.encode("utf-8"))) > _MAX_RESPONSE_URL_BYTES:
            raise ValueError("web response URL is too large")
        if not 0 <= self.redirect_count <= 64:
            raise ValueError("web response redirect count is invalid")
        if not isinstance(self.headers, Mapping) or not isinstance(self.body, AsyncIterable):
            raise TypeError("web response headers or body are invalid")
        _validate_headers(self.headers, max_count=_MAX_RESPONSE_HEADERS, max_bytes=_MAX_RESPONSE_HEADER_BYTES)
        if self.reason is not None and ("\x00" in self.reason or len(self.reason) > 256):
            raise ValueError("web response reason is invalid")
        if len(self.usage) > 64 or not all(isinstance(item, ProviderUsage) for item in self.usage):
            raise TypeError("web response usage is invalid")
        self.usage = tuple(item.model_copy(deep=True) for item in self.usage)

    async def close(self) -> None:
        callback, self._close = self._close, None
        if callback is not None:
            await callback()


class WebSearchResult(BaseModel):
    """One bounded provider-neutral search result."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        revalidate_instances="always",
    )

    title: str = Field(default="", max_length=4096)
    url: str = Field(min_length=1, max_length=16 * 1024)
    snippet: str = Field(default="", max_length=16 * 1024)

    @field_validator("title", "snippet")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("search text must not contain NUL")
        return value

    @field_validator("url")
    @classmethod
    def _validate_result_url(cls, value: str) -> str:
        _require_http_url(value)
        return value


class WebSearchResponse(BaseModel):
    """Search results plus any provider-owned usage receipts."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    results: tuple[WebSearchResult, ...] = Field(max_length=100)
    usage: tuple[ProviderUsage, ...] = Field(default=(), max_length=64)


class WebSearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=4096)
    limit: int = Field(gt=0, le=100)

    @field_validator("query")
    @classmethod
    def _validate_query(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("search query must not contain NUL")
        return value


class WebScrapeRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    url: str = Field(min_length=1, max_length=16 * 1024)
    max_content_bytes: int = Field(gt=0)
    deadline_seconds: float = Field(gt=0, allow_inf_nan=False)
    max_redirects: int = Field(ge=0, le=64)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        _require_http_url(value)
        return value


class WebScrapeResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    content: str
    source_url: str = Field(max_length=16 * 1024)
    canonical_url: str = Field(max_length=16 * 1024)
    title: str | None = Field(default=None, max_length=4096)
    truncated: bool = False
    usage: tuple[ProviderUsage, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def _validate_result(self) -> WebScrapeResult:
        if "\x00" in self.content or (self.title is not None and "\x00" in self.title):
            raise ValueError("scraped content must not contain NUL")
        _require_http_url(self.source_url)
        _require_audience_safe_url(self.canonical_url)
        return self


class WebProviderError(Exception):
    """Stable provider or policy failure safe to expose as a code."""

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or not code.strip() or len(code) > 128:
            raise ValueError("web error code must be a short non-blank string")
        self.code = code
        super().__init__(code)


@runtime_checkable
class WebPolicy(Protocol):
    """Host policy that resolves and authorizes one URL for one purpose."""

    async def authorize(self, url: str, *, purpose: WebPurpose) -> None: ...


@dataclass(frozen=True, slots=True)
class WebDomainPolicy:
    domains: DomainRestrictions
    policy: WebPolicy

    def check_domain(self, url: str) -> None:
        """Check one hop before a transport performs resolution or network I/O."""
        if not self.domains.allows(url):
            raise WebProviderError("web_domain_denied")

    async def authorize(self, url: str, *, purpose: WebPurpose) -> None:
        self.check_domain(url)
        await self.policy.authorize(url, purpose=purpose)


@runtime_checkable
class WebClient(Protocol):
    """Async transport that reapplies policy after resolution and before every redirect hop."""

    async def request(self, request: WebRequest, *, policy: WebPolicy) -> WebResponse: ...


@runtime_checkable
class WebSearchProvider(Protocol):
    async def search(
        self,
        request: WebSearchRequest,
    ) -> WebSearchResponse | Sequence[WebSearchResult]: ...


@runtime_checkable
class WebScrapeProvider(Protocol):
    """Scraper that applies the supplied policy to its initial and redirected fetches."""

    async def scrape(self, request: WebScrapeRequest, *, policy: WebPolicy) -> WebScrapeResult: ...
