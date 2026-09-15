"""Public contracts and backend selection for the Web Toolset."""

from __future__ import annotations

import os
from collections.abc import AsyncIterable, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Literal, NotRequired, Protocol, TypedDict, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from a13n_harness._urls import require_audience_safe_url as _require_audience_safe_url
from a13n_harness._urls import require_http_url as _require_http_url
from a13n_harness.errors import DefinitionError
from a13n_harness.usage import ProviderUsage

from ._results import ToolFailure
from .domains import DomainRestrictions
from .output import ToolOutputDisclosure

_MAX_RESPONSE_HEADERS = 256
_MAX_RESPONSE_HEADER_BYTES = 256 * 1024
_MAX_RESPONSE_URL_BYTES = 16 * 1024

WEB_SEARCH_MODE_ENV = "A13N_HARNESS_WEB_SEARCH_MODE"
WEB_SEARCH_BACKEND_ENV = "A13N_HARNESS_WEB_SEARCH_BACKEND"
WEB_SEARCH_BACKEND_PRIORITY_ENV = "A13N_HARNESS_WEB_SEARCH_BACKEND_PRIORITY"
WEB_SEARCH_CONTEXT_SIZE_ENV = "A13N_HARNESS_WEB_SEARCH_CONTEXT_SIZE"
WEB_SCRAPE_MODE_ENV = "A13N_HARNESS_WEB_SCRAPE_MODE"
WEB_SCRAPE_BACKEND_ENV = "A13N_HARNESS_WEB_SCRAPE_BACKEND"
WEB_SCRAPE_BACKEND_PRIORITY_ENV = "A13N_HARNESS_WEB_SCRAPE_BACKEND_PRIORITY"


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
type WebSearchMode = Literal["off", "host", "native", "auto"]
type WebScrapeMode = Literal["off", "host"]
type WebSearchContextSize = Literal["low", "medium", "high"]


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


class _WebPolicyFailure(Exception):
    """A policy failure that must escape backend fallback unchanged."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        super().__init__(str(error))


@dataclass(frozen=True, slots=True)
class _FallbackBlockingWebPolicy:
    policy: WebPolicy

    async def authorize(self, url: str, *, purpose: WebPurpose) -> None:
        try:
            await self.policy.authorize(url, purpose=purpose)
        except Exception as exc:
            raise _WebPolicyFailure(exc) from exc


def _validate_backend_preference(backend: str | None, priority: tuple[str, ...]) -> None:
    if backend is not None and priority:
        raise ValueError("backend and backend_priority are mutually exclusive")
    if len(set(priority)) != len(priority):
        raise ValueError("backend_priority entries must be unique")


class WebSearchConfiguration(DomainRestrictions):
    """Definition-owned native/Host mode and Host backend preference."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    mode: WebSearchMode = "auto"
    backend: str | None = Field(default=None, min_length=1)
    backend_priority: tuple[Annotated[str, Field(min_length=1)], ...] = ()
    search_context_size: WebSearchContextSize = "medium"

    @model_validator(mode="after")
    def _validate_backend_selection(self) -> WebSearchConfiguration:
        _validate_backend_preference(self.backend, self.backend_priority)
        if self.restricted and self.mode == "native":
            raise ValueError("Domain-restricted search requires Host execution")
        if self.mode in {"off", "native"} and (self.backend is not None or self.backend_priority):
            raise ValueError("Host backend preferences require search mode 'host' or 'auto'")
        return self


class WebScrapeConfiguration(DomainRestrictions):
    """Definition-owned Host scrape mode and backend preference."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    mode: WebScrapeMode = "host"
    backend: str | None = Field(default=None, min_length=1)
    backend_priority: tuple[Annotated[str, Field(min_length=1)], ...] = ()

    @model_validator(mode="after")
    def _validate_backend_selection(self) -> WebScrapeConfiguration:
        _validate_backend_preference(self.backend, self.backend_priority)
        if self.mode == "off" and (self.backend is not None or self.backend_priority):
            raise ValueError("Host backend preferences require scrape mode 'host'")
        return self


class WebFetchConfiguration(DomainRestrictions):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True


class WebDownloadConfiguration(DomainRestrictions):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True


class WebConfiguration(BaseModel):
    """Definition-owned tool selection, redirect, deadline, concurrency, and output bounds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    search: WebSearchConfiguration = Field(default_factory=WebSearchConfiguration)
    scrape: WebScrapeConfiguration = Field(default_factory=WebScrapeConfiguration)
    fetch: WebFetchConfiguration = Field(default_factory=WebFetchConfiguration)
    download: WebDownloadConfiguration = Field(default_factory=WebDownloadConfiguration)
    deadline_seconds: float = Field(default=60.0, gt=0, le=600, allow_inf_nan=False)
    max_redirects: int = Field(default=8, ge=0, le=32)
    max_text_bytes: int = Field(default=256 * 1024, gt=0, le=256 * 1024)
    max_download_bytes: int = Field(default=256 * 1024 * 1024, gt=0, le=4 * 1024 * 1024 * 1024)
    max_download_urls: int = Field(default=16, gt=0, le=128)
    download_concurrency: int = Field(default=4, gt=0, le=32)
    max_search_results: int = Field(default=10, gt=0, le=100)
    max_scrape_bytes: int = Field(default=512 * 1024, gt=0, le=4 * 1024 * 1024)
    stream_chunk_size: int = Field(default=64 * 1024, gt=0, le=1024 * 1024)
    max_response_headers: int = Field(default=128, gt=0, le=_MAX_RESPONSE_HEADERS)
    max_response_header_bytes: int = Field(default=64 * 1024, gt=0, le=_MAX_RESPONSE_HEADER_BYTES)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> WebConfiguration:
        """Build default search and scrape selection from process environment values."""
        source = os.environ if environ is None else environ
        search_backend = _optional_env_value(source, WEB_SEARCH_BACKEND_ENV)
        scrape_backend = _optional_env_value(source, WEB_SCRAPE_BACKEND_ENV)
        return cls(
            search=WebSearchConfiguration.model_validate(
                {
                    "mode": _optional_env_value(source, WEB_SEARCH_MODE_ENV) or "auto",
                    "backend": search_backend,
                    "backend_priority": (
                        ()
                        if search_backend is not None
                        else _backend_priority_from_environment(source, WEB_SEARCH_BACKEND_PRIORITY_ENV)
                    ),
                    "search_context_size": _optional_env_value(source, WEB_SEARCH_CONTEXT_SIZE_ENV) or "medium",
                }
            ),
            scrape=WebScrapeConfiguration.model_validate(
                {
                    "mode": _optional_env_value(source, WEB_SCRAPE_MODE_ENV) or "host",
                    "backend": scrape_backend,
                    "backend_priority": (
                        ()
                        if scrape_backend is not None
                        else _backend_priority_from_environment(source, WEB_SCRAPE_BACKEND_PRIORITY_ENV)
                    ),
                }
            ),
        )


def _optional_env_value(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name)
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _backend_priority_from_environment(environ: Mapping[str, str], name: str) -> tuple[str, ...]:
    value = _optional_env_value(environ, name)
    if value is None:
        return ()
    return tuple(part.strip() for part in value.split(","))


class WebSearchItem(TypedDict):
    title: str
    url: str
    snippet: str


class WebSearchSuccess(TypedDict):
    ok: Literal[True]
    results: list[WebSearchItem]
    showing: int
    disclosure: NotRequired[ToolOutputDisclosure]


class WebScrapeSuccess(TypedDict):
    ok: Literal[True]
    content: str
    source_url: str
    canonical_url: str
    title: str | None
    truncated: bool
    bytes: int
    disclosure: NotRequired[ToolOutputDisclosure]


class WebHeadSuccess(TypedDict):
    ok: Literal[True]
    exists: bool
    status_code: int
    content_type: str | None
    content_length: int | None
    last_modified: str | None
    final_url: str


class WebTextSuccess(TypedDict):
    ok: Literal[True]
    content: str
    truncated: bool
    max_bytes: int
    bytes: int
    final_url: str
    disclosure: NotRequired[ToolOutputDisclosure]


class WebStatusFailure(ToolFailure):
    final_url: str
    content_type: str | None


class WebDownloadSuccess(TypedDict):
    ok: Literal[True]
    url: str
    final_url: str
    save_path: str
    size: int
    content_type: str | None


class WebDownloadFailure(ToolFailure):
    url: str


class WebDownloadStatusFailure(WebStatusFailure):
    url: str


type WebSearchToolResult = WebSearchSuccess | ToolFailure
type WebScrapeToolResult = WebScrapeSuccess | ToolFailure
type WebFetchToolResult = WebHeadSuccess | WebTextSuccess | WebStatusFailure | ToolFailure
type WebDownloadItemResult = WebDownloadSuccess | WebDownloadFailure | WebDownloadStatusFailure | ToolFailure
type WebDownloadToolResult = list[WebDownloadItemResult]


@dataclass(frozen=True, slots=True)
class WebSearchBackendBinding:
    """One named Host search backend in fallback priority order."""

    backend_id: str
    provider: WebSearchProvider

    def __post_init__(self) -> None:
        _validate_backend_id(self.backend_id)
        if not isinstance(self.provider, WebSearchProvider):
            raise TypeError("provider must implement WebSearchProvider")


@dataclass(frozen=True, slots=True)
class WebScrapeBackendBinding:
    """One named Host scrape backend in fallback priority order."""

    backend_id: str
    provider: WebScrapeProvider
    supports_domain_restrictions: bool = False

    def __post_init__(self) -> None:
        _validate_backend_id(self.backend_id)
        if not isinstance(self.provider, WebScrapeProvider):
            raise TypeError("provider must implement WebScrapeProvider")


@dataclass(frozen=True, slots=True)
class WebToolBinding:
    client: WebClient
    policy: WebPolicy
    search_backends: tuple[WebSearchBackendBinding, ...] = ()
    scrape_backends: tuple[WebScrapeBackendBinding, ...] = ()


def _validate_backend_id(backend_id: str) -> None:
    if not isinstance(backend_id, str) or backend_id != backend_id.strip() or not backend_id:
        raise ValueError("backend_id must be a non-blank normalized string")


def _validate_search_backend_bindings(
    backends: Sequence[WebSearchBackendBinding],
) -> tuple[WebSearchBackendBinding, ...]:
    values = tuple(backends)
    if not all(isinstance(item, WebSearchBackendBinding) for item in values):
        raise TypeError("search_backends must contain WebSearchBackendBinding values")
    if len({item.backend_id for item in values}) != len(values):
        raise ValueError("search backend IDs must be unique")
    return values


def _validate_scrape_backend_bindings(
    backends: Sequence[WebScrapeBackendBinding],
) -> tuple[WebScrapeBackendBinding, ...]:
    values = tuple(backends)
    if not all(isinstance(item, WebScrapeBackendBinding) for item in values):
        raise TypeError("scrape_backends must contain WebScrapeBackendBinding values")
    if len({item.backend_id for item in values}) != len(values):
        raise ValueError("scrape backend IDs must be unique")
    return values


def _select_search_backend_bindings(
    backends: tuple[WebSearchBackendBinding, ...],
    configuration: WebSearchConfiguration,
) -> tuple[WebSearchBackendBinding, ...]:
    if configuration.mode not in {"host", "auto"}:
        return ()
    if configuration.backend is not None:
        for backend in backends:
            if backend.backend_id == configuration.backend:
                return (backend,)
        raise DefinitionError(
            "The selected Web search backend is not bound for this run.",
            code="web_search_backend_missing",
            details={"backend_id": configuration.backend},
        )
    by_id = {backend.backend_id: backend for backend in backends}
    prioritized = [by_id[backend_id] for backend_id in configuration.backend_priority if backend_id in by_id]
    selected = {backend.backend_id for backend in prioritized}
    return (*prioritized, *(backend for backend in backends if backend.backend_id not in selected))


def _select_scrape_backend_bindings(
    backends: tuple[WebScrapeBackendBinding, ...],
    configuration: WebScrapeConfiguration,
) -> tuple[WebScrapeBackendBinding, ...]:
    if configuration.mode == "off":
        return ()
    if configuration.backend is not None:
        for backend in backends:
            if backend.backend_id == configuration.backend:
                return (backend,)
        raise DefinitionError(
            "The selected Web scrape backend is not bound for this run.",
            code="web_scrape_backend_missing",
            details={"backend_id": configuration.backend},
        )
    by_id = {backend.backend_id: backend for backend in backends}
    prioritized = [by_id[backend_id] for backend_id in configuration.backend_priority if backend_id in by_id]
    selected = {backend.backend_id for backend in prioritized}
    return (*prioritized, *(backend for backend in backends if backend.backend_id not in selected))
