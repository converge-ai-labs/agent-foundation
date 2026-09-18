"""Public contracts and backend selection for the Web Toolset."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

from a13n_harness.errors import DefinitionError
from a13n_harness.providers.web.contracts import (
    _MAX_RESPONSE_HEADER_BYTES,
    _MAX_RESPONSE_HEADERS,
    WebClient,
    WebDomainPolicy,
    WebMethod,
    WebPolicy,
    WebProviderError,
    WebPurpose,
    WebRequest,
    WebResponse,
    WebScrapeProvider,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchProvider,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)
from a13n_harness.providers.web.domains import DomainRestrictions

from ._results import ToolFailure
from .output import ToolOutputDisclosure

WEB_SEARCH_MODE_ENV = "A13N_HARNESS_WEB_SEARCH_MODE"
WEB_SEARCH_BACKEND_ENV = "A13N_HARNESS_WEB_SEARCH_BACKEND"
WEB_SEARCH_BACKEND_PRIORITY_ENV = "A13N_HARNESS_WEB_SEARCH_BACKEND_PRIORITY"
WEB_SEARCH_CONTEXT_SIZE_ENV = "A13N_HARNESS_WEB_SEARCH_CONTEXT_SIZE"
WEB_SCRAPE_MODE_ENV = "A13N_HARNESS_WEB_SCRAPE_MODE"
WEB_SCRAPE_BACKEND_ENV = "A13N_HARNESS_WEB_SCRAPE_BACKEND"
WEB_SCRAPE_BACKEND_PRIORITY_ENV = "A13N_HARNESS_WEB_SCRAPE_BACKEND_PRIORITY"


type WebSearchMode = Literal["off", "host", "native", "auto"]
type WebScrapeMode = Literal["off", "host"]
type WebSearchContextSize = Literal["low", "medium", "high"]


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


__all__ = [
    "WebClient",
    "WebDomainPolicy",
    "WebMethod",
    "WebPolicy",
    "WebProviderError",
    "WebPurpose",
    "WebRequest",
    "WebResponse",
    "WebScrapeProvider",
    "WebScrapeRequest",
    "WebScrapeResult",
    "WebSearchProvider",
    "WebSearchRequest",
    "WebSearchResponse",
    "WebSearchResult",
]
