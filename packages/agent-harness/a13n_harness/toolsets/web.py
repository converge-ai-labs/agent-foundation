"""Reusable policy-bound Web Toolset over asynchronous provider ports."""

from __future__ import annotations

import asyncio
import mimetypes
import posixpath
from collections.abc import AsyncIterable, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Literal, NotRequired, Protocol, TypedDict, cast, runtime_checkable
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from pydantic_ai import BinaryContent, RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness._urls import project_audience_safe_url as _safe_url
from a13n_harness._urls import require_audience_safe_url as _require_audience_safe_url
from a13n_harness._urls import require_http_url as _require_http_url
from a13n_harness.context import AgentContext
from a13n_harness.environment.files import FileOperator
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.providers import FileScopeProvider
from a13n_harness.errors import RunError
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolEffect, ToolOutputPolicy
from a13n_harness.usage import ProviderUsage

from ._results import ToolError, ToolFailure
from ._scoped_files import ScopedFileAccess
from .output import ToolOutputDisclosure, disclose_sequence_field, disclose_text_fields

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_AMBIGUOUS_MEDIA_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})
_MAX_RESPONSE_HEADERS = 256
_MAX_RESPONSE_HEADER_BYTES = 256 * 1024
_MAX_RESPONSE_URL_BYTES = 16 * 1024
_CLOSE_GRACE_SECONDS = 0.05

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
    max_markdown_bytes: int = Field(gt=0)
    deadline_seconds: float = Field(gt=0, allow_inf_nan=False)
    max_redirects: int = Field(ge=0, le=64)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        _require_http_url(value)
        return value


class WebScrapeResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    markdown: str
    final_url: str = Field(max_length=16 * 1024)
    canonical_url: str = Field(max_length=16 * 1024)
    usage: tuple[ProviderUsage, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def _validate_result(self) -> WebScrapeResult:
        if "\x00" in self.markdown:
            raise ValueError("scraped Markdown must not contain NUL")
        _require_http_url(self.final_url)
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


class WebConfiguration(BaseModel):
    """Definition-owned redirect, deadline, concurrency, and output bounds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deadline_seconds: float = Field(default=60.0, gt=0, le=600, allow_inf_nan=False)
    max_redirects: int = Field(default=8, ge=0, le=32)
    max_text_bytes: int = Field(default=256 * 1024, gt=0, le=256 * 1024)
    max_inline_binary_bytes: int = Field(default=4 * 1024 * 1024, gt=0, le=4 * 1024 * 1024)
    max_download_bytes: int = Field(default=256 * 1024 * 1024, gt=0, le=4 * 1024 * 1024 * 1024)
    max_download_urls: int = Field(default=16, gt=0, le=128)
    download_concurrency: int = Field(default=4, gt=0, le=32)
    max_search_results: int = Field(default=10, gt=0, le=100)
    max_scrape_bytes: int = Field(default=512 * 1024, gt=0, le=4 * 1024 * 1024)
    stream_chunk_size: int = Field(default=64 * 1024, gt=0, le=1024 * 1024)
    max_response_headers: int = Field(default=128, gt=0, le=_MAX_RESPONSE_HEADERS)
    max_response_header_bytes: int = Field(default=64 * 1024, gt=0, le=_MAX_RESPONSE_HEADER_BYTES)


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
    markdown: str
    final_url: str
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
type WebFetchToolResult = WebHeadSuccess | WebTextSuccess | WebStatusFailure | ToolFailure | ToolReturn
type WebDownloadItemResult = WebDownloadSuccess | WebDownloadFailure | WebDownloadStatusFailure | ToolFailure
type WebDownloadToolResult = list[WebDownloadItemResult]


@dataclass(frozen=True, slots=True)
class WebToolBinding:
    client: WebClient
    policy: WebPolicy
    search_provider: WebSearchProvider | None = None
    scrape_provider: WebScrapeProvider | None = None


class WebToolset:
    """Standard Web policy, transport, projection, and download semantics."""

    def __init__(
        self,
        *,
        client: WebClient,
        policy: WebPolicy,
        configuration: WebConfiguration | None = None,
        search_provider: WebSearchProvider | None = None,
        scrape_provider: WebScrapeProvider | None = None,
        files: FileOperator,
        file_scopes: FileScopeProvider | None = None,
    ) -> None:
        self.configuration = (configuration or WebConfiguration()).model_copy(deep=True)
        self._binding = WebToolBinding(client, policy, search_provider, scrape_provider)
        self._file_access = ScopedFileAccess(files, file_scopes)

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        tools: list[HarnessTool] = []
        if self._binding.search_provider is not None:
            tools.append(
                self._tool(
                    self.search,
                    tool_id="web.search",
                    name="search",
                    effects=frozenset({"read", "external_communication"}),
                )
            )
        if self._binding.scrape_provider is not None:
            tools.append(
                self._tool(
                    self.scrape,
                    tool_id="web.scrape",
                    name="scrape",
                    effects=frozenset({"read", "external_communication"}),
                )
            )
        tools.extend(
            (
                self._tool(
                    self.fetch,
                    tool_id="web.fetch",
                    name="fetch",
                    effects=frozenset({"read", "external_communication"}),
                ),
                self._tool(
                    self.download,
                    tool_id="web.download",
                    name="download",
                    effects=frozenset({"read", "write", "external_communication"}),
                ),
            )
        )
        return FunctionToolset(tools=tools, id="a13n-web-tool-functions")

    def _tool(self, function, *, tool_id: str, name: str, effects: frozenset[ToolEffect]) -> HarnessTool:
        return HarnessTool(
            function,
            harness_metadata=HarnessToolMetadata(
                tool_id=tool_id,
                effects=effects,
                credential_audiences=(),
                idempotency="read_only" if "write" not in effects else "none",
                output_policy=ToolOutputPolicy(
                    max_inline_bytes=256 * 1024,
                    max_output_bytes=4 * 1024 * 1024,
                    overflow="truncate" if name != "fetch" else "fail",
                    redact=True,
                ),
                resource_resolver=(self._file_access.resource_resolver("save_dir") if name == "download" else None),
            ),
            name=name,
        )

    async def search(
        self,
        ctx: RunContext[AgentContext],
        query: Annotated[str, Field(description="Search query")],
        num: Annotated[int | None, Field(default=None, ge=1, le=100)] = None,
    ) -> WebSearchToolResult:
        attachment = self._binding
        provider = attachment.search_provider
        if provider is None:
            return _web_error("web_search_unavailable")
        limit = num or self.configuration.max_search_results
        limit = min(limit, self.configuration.max_search_results)
        try:
            deadline = _operation_deadline(self.configuration.deadline_seconds)
            async with asyncio.timeout(_remaining_seconds(deadline)):
                raw = await provider.search(WebSearchRequest(query=query, limit=limit))
            if isinstance(raw, WebSearchResponse):
                response = WebSearchResponse.model_validate(raw)
            else:
                if not isinstance(raw, Sequence) or len(raw) > limit:
                    raise WebProviderError("web_search_response_invalid")
                response = WebSearchResponse(results=tuple(raw))
            for usage in response.usage:
                await ctx.deps.record_provider_usage(
                    usage,
                    source="web.search",
                    tool_id="web.search",
                    tool_call_id=ctx.tool_call_id,
                )
            if len(response.results) > limit:
                raise WebProviderError("web_search_response_invalid")
            results = [WebSearchResult.model_validate(item) for item in response.results]
            projected: dict[str, JsonValue] = {
                "ok": True,
                "results": cast(
                    JsonValue,
                    [
                        {
                            "title": item.title,
                            "url": _safe_url(item.url),
                            "snippet": item.snippet,
                        }
                        for item in results
                    ],
                ),
                "showing": len(results),
            }
            disclosed, showing = await disclose_sequence_field(
                ctx.deps,
                projected,
                field="results",
                content_complete=True,
                noun="web search results",
            )
            disclosed["showing"] = showing
            return cast(WebSearchSuccess, disclosed)
        except TimeoutError:
            return _web_error("web_timeout", retry_hint="retry")
        except RunError:
            raise
        except WebProviderError as exc:
            return _web_error(exc.code)
        except (TypeError, ValueError):
            return _web_error("web_search_response_invalid")
        except Exception:
            return _web_error("web_search_failed")

    async def scrape(
        self,
        ctx: RunContext[AgentContext],
        url: Annotated[str, Field(description="HTTP or HTTPS page URL")],
    ) -> WebScrapeToolResult:
        attachment = self._binding
        provider = attachment.scrape_provider
        if provider is None:
            return _web_error("web_scrape_unavailable")
        try:
            deadline = _operation_deadline(self.configuration.deadline_seconds)
            async with asyncio.timeout(_remaining_seconds(deadline)):
                await attachment.policy.authorize(url, purpose="scrape")
            request = WebScrapeRequest(
                url=url,
                max_markdown_bytes=self.configuration.max_scrape_bytes,
                deadline_seconds=_remaining_seconds(deadline),
                max_redirects=self.configuration.max_redirects,
            )
            async with asyncio.timeout(_remaining_seconds(deadline)):
                raw = await provider.scrape(request, policy=attachment.policy)
            result = WebScrapeResult.model_validate(raw)
            for usage in result.usage:
                await ctx.deps.record_provider_usage(
                    usage,
                    source="web.scrape",
                    tool_id="web.scrape",
                    tool_call_id=ctx.tool_call_id,
                )
            encoded = result.markdown.encode("utf-8")
            if len(encoded) > self.configuration.max_scrape_bytes:
                return _web_error("web_body_too_large", max_bytes=self.configuration.max_scrape_bytes)
            async with asyncio.timeout(_remaining_seconds(deadline)):
                await attachment.policy.authorize(result.final_url, purpose="scrape")
            projected: dict[str, JsonValue] = {
                "ok": True,
                "markdown": result.markdown,
                "final_url": result.canonical_url,
                "bytes": len(encoded),
            }
            return cast(
                WebScrapeSuccess,
                await disclose_text_fields(
                    ctx.deps,
                    projected,
                    text_fields=("markdown",),
                    content_complete=True,
                    noun="scraped page",
                ),
            )
        except TimeoutError:
            return _web_error("web_timeout", retry_hint="retry")
        except RunError:
            raise
        except WebProviderError as exc:
            return _web_error(exc.code)
        except (TypeError, ValueError):
            return _web_error("web_scrape_response_invalid")
        except Exception:
            return _web_error("web_scrape_failed")

    async def fetch(
        self,
        ctx: RunContext[AgentContext],
        url: Annotated[str, Field(description="HTTP or HTTPS resource URL")],
        head_only: Annotated[bool, Field(description="Return metadata without retaining a body")] = False,
    ) -> WebFetchToolResult:
        try:
            deadline = _operation_deadline(self.configuration.deadline_seconds)
            method: WebMethod = "HEAD" if head_only else "GET"
            response = await self._request(
                ctx,
                url,
                method=method,
                purpose="fetch",
                max_bytes=self._fetch_limit(),
                deadline=deadline,
            )
            try:
                if head_only:
                    return _head_projection(response)
                if response.status_code >= 400:
                    return _status_error(response)
                content_type = _header(response.headers, "content-type").split(";", maxsplit=1)[0].strip().lower()
                textual = _is_textual(content_type)
                body_limit = (
                    self.configuration.max_text_bytes if textual else self.configuration.max_inline_binary_bytes
                )
                async with asyncio.timeout(_remaining_seconds(deadline)):
                    data, truncated = await _read_response_body(
                        response.body,
                        body_limit,
                        max_chunk_bytes=self.configuration.stream_chunk_size,
                        truncate=textual,
                    )
                if textual:
                    text = data.decode(_text_charset(_header(response.headers, "content-type")), errors="replace")
                    projected: dict[str, JsonValue] = {
                        "ok": True,
                        "content": text,
                        "truncated": truncated,
                        "max_bytes": self.configuration.max_text_bytes,
                        "bytes": len(data),
                        "final_url": response.canonical_url,
                    }
                    return cast(
                        WebTextSuccess,
                        await disclose_text_fields(
                            ctx.deps,
                            projected,
                            text_fields=("content",),
                            content_complete=not truncated,
                            noun="fetched text",
                        ),
                    )
                if truncated:
                    raise WebProviderError("web_body_too_large")
                media_type = content_type or "application/octet-stream"
                return ToolReturn(
                    return_value=(
                        f"The fetched {media_type} resource is attached in the user message.\n\n"
                        f"Canonical source: {response.canonical_url}"
                    ),
                    content=[BinaryContent(data=data, media_type=media_type)],
                )
            finally:
                await _close_response(response)
        except TimeoutError:
            return _web_error("web_timeout", retry_hint="retry")
        except RunError:
            raise
        except WebProviderError as exc:
            return _web_error(exc.code)
        except (TypeError, ValueError):
            return _web_error("web_response_invalid")
        except Exception:
            return _web_error("web_fetch_failed")

    async def download(
        self,
        ctx: RunContext[AgentContext],
        urls: Annotated[Sequence[str], Field(description="HTTP or HTTPS URLs to download", min_length=1)],
        save_dir: Annotated[str, Field(description="Logical Environment directory for downloaded files")],
    ) -> WebDownloadToolResult:
        if len(urls) > self.configuration.max_download_urls:
            return [_web_error("web_download_batch_too_large")]
        try:
            async with self._file_access.scope(save_dir) as files:
                await files.mkdir(save_dir, parents=True, exist_ok=True)
                semaphore = asyncio.Semaphore(self.configuration.download_concurrency)

                async def one(url: str) -> WebDownloadItemResult:
                    async with semaphore:
                        return await self._download_one(ctx, url, save_dir, files)

                return list(await asyncio.gather(*(one(url) for url in urls)))
        except EnvironmentError as exc:
            return [_web_error(exc.code)]

    async def _download_one(
        self,
        ctx: RunContext[AgentContext],
        url: str,
        save_dir: str,
        files: FileOperator,
    ) -> WebDownloadItemResult:
        response: WebResponse | None = None
        total = 0
        safe_requested = _safe_url(url)
        try:
            deadline = _operation_deadline(self.configuration.deadline_seconds)
            response = await self._request(
                ctx,
                url,
                method="GET",
                purpose="download",
                max_bytes=self.configuration.max_download_bytes,
                deadline=deadline,
            )
            if response.status_code >= 400:
                return {"url": safe_requested, **_status_error(response)}
            content_type = _header(response.headers, "content-type").split(";", maxsplit=1)[0].strip().lower()
            extension = _download_extension(content_type, response.canonical_url)
            save_path = f"{save_dir.rstrip('/')}/{uuid4().hex}{extension}"

            async def bounded_stream():
                nonlocal total
                async for chunk in response.body:
                    if not isinstance(chunk, bytes):
                        raise WebProviderError("web_response_invalid")
                    if len(chunk) > self.configuration.stream_chunk_size:
                        raise WebProviderError("web_response_invalid")
                    total += len(chunk)
                    if total > self.configuration.max_download_bytes:
                        raise WebProviderError("web_body_too_large")
                    yield chunk

            self._file_access.guard(save_dir)
            async with asyncio.timeout(_remaining_seconds(deadline)):
                written = await files.write_bytes_stream(
                    save_path,
                    bounded_stream(),
                    mode="create",
                )
            return {
                "ok": True,
                "url": safe_requested,
                "final_url": response.canonical_url,
                "save_path": written.path,
                "size": written.bytes_written,
                "content_type": content_type or None,
            }
        except TimeoutError:
            return {"url": safe_requested, **_web_error("web_timeout", retry_hint="retry")}
        except RunError:
            raise
        except WebProviderError as exc:
            return {
                "url": safe_requested,
                **_web_error(
                    exc.code,
                    max_bytes=self.configuration.max_download_bytes if exc.code == "web_body_too_large" else None,
                ),
            }
        except EnvironmentError as exc:
            return {"url": safe_requested, **_web_error(exc.code)}
        except (TypeError, ValueError):
            return {"url": safe_requested, **_web_error("web_response_invalid")}
        except Exception:
            return {"url": safe_requested, **_web_error("web_download_failed")}
        finally:
            if response is not None:
                await _close_response(response)

    async def _request(
        self,
        ctx: RunContext[AgentContext],
        url: str,
        *,
        method: WebMethod,
        purpose: WebPurpose,
        max_bytes: int,
        deadline: float,
    ) -> WebResponse:
        attachment = self._binding
        async with asyncio.timeout(_remaining_seconds(deadline)):
            await attachment.policy.authorize(url, purpose=purpose)
        request = WebRequest(
            url=url,
            method=method,
            purpose=purpose,
            deadline_seconds=_remaining_seconds(deadline),
            max_redirects=self.configuration.max_redirects,
            max_response_bytes=max_bytes,
            max_header_count=self.configuration.max_response_headers,
            max_header_bytes=self.configuration.max_response_header_bytes,
            max_stream_chunk_bytes=self.configuration.stream_chunk_size,
        )
        async with asyncio.timeout(_remaining_seconds(deadline)):
            response = await attachment.client.request(request, policy=attachment.policy)
        if not isinstance(response, WebResponse):
            raise WebProviderError("web_response_invalid")
        try:
            _validate_response(response, request=request)
            for usage in response.usage:
                await ctx.deps.record_provider_usage(
                    usage,
                    source=f"web.{purpose}",
                    tool_id=f"web.{purpose}",
                    tool_call_id=ctx.tool_call_id,
                )
            if response.redirect_count > self.configuration.max_redirects or response.status_code in _REDIRECT_STATUSES:
                raise WebProviderError("web_redirect_invalid")
            async with asyncio.timeout(_remaining_seconds(deadline)):
                await attachment.policy.authorize(response.final_url, purpose=purpose)
        except BaseException:
            await _close_response(response)
            raise
        return response

    def _fetch_limit(self) -> int:
        return max(self.configuration.max_text_bytes, self.configuration.max_inline_binary_bytes)


def _operation_deadline(seconds: float) -> float:
    return asyncio.get_running_loop().time() + seconds


def _remaining_seconds(deadline: float) -> float:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError
    return remaining


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


def _validate_response(response: WebResponse, *, request: WebRequest) -> None:
    _require_http_url(response.final_url)
    _require_audience_safe_url(response.canonical_url)
    _validate_headers(
        response.headers,
        max_count=request.max_header_count,
        max_bytes=request.max_header_bytes,
    )
    # Detach the finite response envelope from a provider-owned mutable mapping.
    response.headers = dict(response.headers)


def _consume_close_task(task: asyncio.Task[None]) -> None:
    try:
        task.exception()
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def _drain_response_close(response: WebResponse) -> None:
    close_task = asyncio.create_task(response.close())
    done, _ = await asyncio.wait({close_task}, timeout=_CLOSE_GRACE_SECONDS)
    if close_task not in done:
        close_task.cancel()
        done, _ = await asyncio.wait({close_task}, timeout=_CLOSE_GRACE_SECONDS)
    if close_task in done:
        _consume_close_task(close_task)
    else:
        close_task.add_done_callback(_consume_close_task)


async def _close_response(response: WebResponse) -> None:
    drain_task = asyncio.create_task(_drain_response_close(response))
    try:
        await asyncio.shield(drain_task)
    except asyncio.CancelledError:
        drain_task.add_done_callback(_consume_close_task)
        raise


def _header(headers: Mapping[str, str], name: str) -> str:
    lowered = name.casefold()
    for key, value in headers.items():
        if key.casefold() == lowered:
            return value
    return ""


def _is_textual(media_type: str) -> bool:
    return (
        media_type.startswith("text/")
        or media_type in {"application/json", "application/xml", "application/javascript"}
        or media_type.endswith("+json")
        or media_type.endswith("+xml")
    )


def _text_charset(content_type: str) -> str:
    for parameter in content_type.split(";")[1:]:
        key, separator, value = parameter.partition("=")
        if separator and key.strip().casefold() == "charset":
            candidate = value.strip().strip('"').casefold()
            if candidate in {"utf-8", "utf8", "ascii", "us-ascii"}:
                return candidate
    return "utf-8"


async def _read_response_body(
    body: AsyncIterable[bytes],
    limit: int,
    *,
    max_chunk_bytes: int,
    truncate: bool,
) -> tuple[bytes, bool]:
    output = bytearray()
    async for chunk in body:
        if not isinstance(chunk, bytes) or len(chunk) > max_chunk_bytes:
            raise WebProviderError("web_response_invalid")
        remaining = limit - len(output)
        if len(chunk) > remaining:
            if truncate:
                output.extend(chunk[:remaining])
                return bytes(output), True
            raise WebProviderError("web_body_too_large")
        output.extend(chunk)
    return bytes(output), False


def _head_projection(response: WebResponse) -> WebHeadSuccess:
    return {
        "ok": True,
        "exists": response.status_code < 400,
        "status_code": response.status_code,
        "content_type": _header(response.headers, "content-type") or None,
        "content_length": _bounded_header_int(response.headers, "content-length"),
        "last_modified": (_header(response.headers, "last-modified") or None),
        "final_url": response.canonical_url,
    }


def _bounded_header_int(headers: Mapping[str, str], name: str) -> int | None:
    try:
        value = int(_header(headers, name))
    except ValueError:
        return None
    return value if 0 <= value <= 4 * 1024 * 1024 * 1024 else None


def _status_error(response: WebResponse) -> WebStatusFailure:
    return {
        "ok": False,
        "error": {
            "code": "web_http_status",
            "status_code": response.status_code,
            "reason": response.reason or None,
            "retry_hint": "retry" if response.status_code >= 500 or response.status_code == 429 else "request_change",
        },
        "final_url": response.canonical_url,
        "content_type": _header(response.headers, "content-type").split(";", maxsplit=1)[0].strip() or None,
    }


def _download_extension(content_type: str, final_url: str) -> str:
    if content_type not in _AMBIGUOUS_MEDIA_TYPES:
        guessed = mimetypes.guess_extension(content_type)
        if guessed is not None and len(guessed) <= 16:
            return guessed
    suffix = posixpath.splitext(urlsplit(final_url).path)[1]
    if 1 < len(suffix) <= 16 and suffix[1:].isalnum():
        return suffix.lower()
    return ""


def _web_error(
    code: str,
    *,
    retry_hint: str = "dependency_change",
    max_bytes: int | None = None,
) -> ToolFailure:
    error: ToolError = {"code": code, "retry_hint": retry_hint}
    if max_bytes is not None:
        error["max_bytes"] = max_bytes
    return {"ok": False, "error": error}


__all__ = [
    "WebClient",
    "WebConfiguration",
    "WebDownloadItemResult",
    "WebDownloadToolResult",
    "WebFetchToolResult",
    "WebPolicy",
    "WebProviderError",
    "WebRequest",
    "WebResponse",
    "WebScrapeProvider",
    "WebScrapeRequest",
    "WebScrapeResult",
    "WebScrapeToolResult",
    "WebSearchProvider",
    "WebSearchRequest",
    "WebSearchResponse",
    "WebSearchResult",
    "WebSearchToolResult",
    "WebStatusFailure",
    "WebToolset",
]
