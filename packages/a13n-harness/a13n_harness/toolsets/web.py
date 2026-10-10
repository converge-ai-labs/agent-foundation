"""Reusable policy-bound Web Toolset over asynchronous provider ports."""

from __future__ import annotations

import asyncio
import mimetypes
import posixpath
from collections.abc import AsyncIterable, Callable, Mapping, Sequence
from dataclasses import replace
from typing import Annotated, cast
from urllib.parse import urlsplit
from uuid import uuid4

from a13n_environment.files import FileOperator
from a13n_environment.models import EnvironmentError
from pydantic import Field, JsonValue
from pydantic_ai import RunContext
from pydantic_ai.native_tools import WebSearchTool
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness._urls import project_audience_safe_url as _safe_url
from a13n_harness._urls import require_audience_safe_url as _require_audience_safe_url
from a13n_harness._urls import require_http_url as _require_http_url
from a13n_harness.context import AgentContext
from a13n_harness.environment.providers import FileScopeProvider
from a13n_harness.errors import DefinitionError, RunError
from a13n_harness.media_types import is_text_media_type, text_charset
from a13n_harness.providers.web.contracts import _validate_headers
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolEffect, ToolOutputPolicy

from ._instructions import InstructionFunctionToolset, tool_instruction
from ._results import ToolFailure, environment_failure, tool_failure
from ._scoped_files import ScopedFileAccess
from .output import disclose_sequence_field, disclose_text_fields
from .web_contracts import (
    WEB_SCRAPE_BACKEND_ENV,
    WEB_SCRAPE_BACKEND_PRIORITY_ENV,
    WEB_SCRAPE_MODE_ENV,
    WEB_SEARCH_BACKEND_ENV,
    WEB_SEARCH_BACKEND_PRIORITY_ENV,
    WEB_SEARCH_CONTEXT_SIZE_ENV,
    WEB_SEARCH_MODE_ENV,
    WebClient,
    WebConfiguration,
    WebDomainPolicy,
    WebDownloadConfiguration,
    WebDownloadItemResult,
    WebDownloadToolResult,
    WebFetchConfiguration,
    WebFetchToolResult,
    WebHeadSuccess,
    WebMethod,
    WebPolicy,
    WebProviderError,
    WebPurpose,
    WebRequest,
    WebResponse,
    WebScrapeBackendBinding,
    WebScrapeConfiguration,
    WebScrapeProvider,
    WebScrapeRequest,
    WebScrapeResult,
    WebScrapeSuccess,
    WebScrapeToolResult,
    WebSearchBackendBinding,
    WebSearchConfiguration,
    WebSearchProvider,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
    WebSearchSuccess,
    WebSearchToolResult,
    WebStatusFailure,
    WebTextSuccess,
    WebToolBinding,
    _FallbackBlockingWebPolicy,
    _select_scrape_backend_bindings,
    _select_search_backend_bindings,
    _validate_scrape_backend_bindings,
    _validate_search_backend_bindings,
    _WebPolicyFailure,
)

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_AMBIGUOUS_MEDIA_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})
_CLOSE_GRACE_SECONDS = 0.05
# Cancellation-resistant provider cleanup can outlive its caller. Keep both
# drain and close tasks rooted until completion, then release them in the callback.
_RESPONSE_CLOSE_TASKS: set[asyncio.Task[None]] = set()

_SEARCH_INSTRUCTION = tool_instruction("search")
_SCRAPE_INSTRUCTION = tool_instruction("scrape")
_FETCH_INSTRUCTION = tool_instruction("fetch")
_DOWNLOAD_INSTRUCTION = tool_instruction("download")


def _prefer_native_web_search(
    _ctx: RunContext[AgentContext],
    tool_def: ToolDefinition,
) -> ToolDefinition:
    return replace(tool_def, unless_native=WebSearchTool.kind)


class WebToolset:
    """Standard Web policy, transport, projection, and download semantics."""

    def __init__(
        self,
        *,
        client: WebClient,
        policy: WebPolicy,
        configuration: WebConfiguration | None = None,
        search_backends: Sequence[WebSearchBackendBinding] = (),
        scrape_backends: Sequence[WebScrapeBackendBinding] = (),
        files: FileOperator,
        file_scopes: FileScopeProvider | None = None,
    ) -> None:
        self.configuration = (configuration or WebConfiguration()).model_copy(deep=True)
        validated_search_backends = _validate_search_backend_bindings(tuple(search_backends))
        validated_scrape_backends = _validate_scrape_backend_bindings(tuple(scrape_backends))
        if (
            self.configuration.search.restricted
            and self.configuration.search.mode != "off"
            and not validated_search_backends
        ):
            raise ValueError("Domain-restricted search requires a Host search backend")
        selected_scrape_backends = _select_scrape_backend_bindings(validated_scrape_backends, self.configuration.scrape)
        if self.configuration.scrape.restricted and any(
            not backend.supports_domain_restrictions for backend in selected_scrape_backends
        ):
            raise DefinitionError(
                "The selected Web scrape backend cannot enforce domain restrictions.",
                code="web_scrape_domain_restrictions_unsupported",
            )
        self._binding = WebToolBinding(
            client,
            policy,
            _select_search_backend_bindings(validated_search_backends, self.configuration.search),
            selected_scrape_backends,
        )
        self._file_access = ScopedFileAccess(files, file_scopes)

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        tools: list[HarnessTool] = []
        instructions: list[str] = []
        search_mode = self.configuration.search.mode
        host_search = search_mode in {"host", "auto"} and bool(self._binding.search_backends)
        search_enabled = host_search or search_mode in {"native", "auto"}
        if search_enabled:
            instructions.append(_SEARCH_INSTRUCTION)
        if host_search:
            tools.append(
                self._tool(
                    self.search,
                    tool_id="web.search",
                    name="search",
                    effects=frozenset({"read", "external_communication"}),
                    prepare=_prefer_native_web_search
                    if search_mode == "auto" and not self.configuration.search.restricted
                    else None,
                )
            )
        if self.configuration.scrape.mode == "host" and self._binding.scrape_backends:
            instructions.append(_SCRAPE_INSTRUCTION)
            tools.append(
                self._tool(
                    self.scrape,
                    tool_id="web.scrape",
                    name="scrape",
                    effects=frozenset({"read", "external_communication"}),
                )
            )
        if self.configuration.fetch.enabled:
            instructions.append(_FETCH_INSTRUCTION)
            tools.append(
                self._tool(
                    self.fetch,
                    tool_id="web.fetch",
                    name="fetch",
                    effects=frozenset({"read", "external_communication"}),
                )
            )
        if self.configuration.download.enabled:
            instructions.append(_DOWNLOAD_INSTRUCTION)
            tools.append(
                self._tool(
                    self.download,
                    tool_id="web.download",
                    name="download",
                    effects=frozenset({"read", "write", "external_communication"}),
                )
            )
        return InstructionFunctionToolset(
            tools=tools,
            id="a13n-web-tool-functions",
            instructions=instructions,
        )

    def _tool(
        self,
        function,
        *,
        tool_id: str,
        name: str,
        effects: frozenset[ToolEffect],
        prepare: Callable[[RunContext[AgentContext], ToolDefinition], ToolDefinition] | None = None,
    ) -> HarnessTool:
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
            prepare=prepare,
        )

    async def _search_with_backends(
        self,
        request: WebSearchRequest,
        *,
        deadline: float,
    ) -> WebSearchResponse:
        last_error = "web_search_failed"
        for backend in self._binding.search_backends:
            try:
                async with asyncio.timeout(_remaining_seconds(deadline)):
                    raw = await backend.provider.search(request)
                if isinstance(raw, WebSearchResponse):
                    response = WebSearchResponse.model_validate(raw)
                else:
                    if not isinstance(raw, Sequence) or len(raw) > request.limit:
                        raise WebProviderError("web_search_response_invalid")
                    response = WebSearchResponse(results=tuple(raw))
                if len(response.results) > request.limit:
                    raise WebProviderError("web_search_response_invalid")
                return response
            except TimeoutError:
                raise
            except RunError:
                raise
            except WebProviderError as exc:
                last_error = exc.code
            except (TypeError, ValueError):
                last_error = "web_search_response_invalid"
            except Exception:
                last_error = "web_search_failed"
        raise WebProviderError(last_error)

    async def _scrape_with_backends(
        self,
        request: WebScrapeRequest,
        *,
        deadline: float,
        policy: WebPolicy,
    ) -> WebScrapeResult:
        last_error = "web_scrape_failed"
        blocking_policy = _FallbackBlockingWebPolicy(policy)
        for backend in self._binding.scrape_backends:
            try:
                async with asyncio.timeout(_remaining_seconds(deadline)):
                    raw = await backend.provider.scrape(request, policy=blocking_policy)
                    result = WebScrapeResult.model_validate(raw)
                    if len(result.content.encode("utf-8")) > request.max_content_bytes:
                        raise WebProviderError("web_body_too_large")
                    await blocking_policy.authorize(result.source_url, purpose="scrape")
                    if isinstance(policy, WebDomainPolicy):
                        try:
                            policy.check_domain(result.canonical_url)
                        except Exception as exc:
                            raise _WebPolicyFailure(exc) from exc
                return result
            except _WebPolicyFailure as exc:
                raise exc.error from exc
            except TimeoutError:
                raise
            except RunError:
                raise
            except WebProviderError as exc:
                last_error = exc.code
            except (TypeError, ValueError):
                last_error = "web_scrape_response_invalid"
            except Exception:
                last_error = "web_scrape_failed"
        raise WebProviderError(last_error)

    async def search(
        self,
        ctx: RunContext[AgentContext],
        query: Annotated[str, Field(description="Search query")],
        num: Annotated[int | None, Field(default=None, ge=1, le=100)] = None,
    ) -> WebSearchToolResult:
        if not self._binding.search_backends:
            return _web_error("web_search_unavailable")
        limit = num or self.configuration.max_search_results
        limit = min(limit, self.configuration.max_search_results)
        try:
            deadline = _operation_deadline(self.configuration.deadline_seconds)
            response = await self._search_with_backends(
                WebSearchRequest(query=query, limit=limit),
                deadline=deadline,
            )
            for usage in response.usage:
                await ctx.deps.record_provider_usage(
                    usage,
                    source="web.search",
                    tool_id="web.search",
                    tool_call_id=ctx.tool_call_id,
                )
            results = [WebSearchResult.model_validate(item) for item in response.results]
            results = [item for item in results if self.configuration.search.allows(item.url)]
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
        if not attachment.scrape_backends:
            return _web_error("web_scrape_unavailable")
        try:
            deadline = _operation_deadline(self.configuration.deadline_seconds)
            policy = (
                WebDomainPolicy(self.configuration.scrape, attachment.policy)
                if self.configuration.scrape.restricted
                else attachment.policy
            )
            async with asyncio.timeout(_remaining_seconds(deadline)):
                await policy.authorize(url, purpose="scrape")
            request = WebScrapeRequest(
                url=url,
                max_content_bytes=self.configuration.max_scrape_bytes,
                deadline_seconds=_remaining_seconds(deadline),
                max_redirects=self.configuration.max_redirects,
            )
            result = await self._scrape_with_backends(request, deadline=deadline, policy=policy)
            for usage in result.usage:
                await ctx.deps.record_provider_usage(
                    usage,
                    source="web.scrape",
                    tool_id="web.scrape",
                    tool_call_id=ctx.tool_call_id,
                )
            encoded = result.content.encode("utf-8")
            if len(encoded) > self.configuration.max_scrape_bytes:
                return _web_error("web_body_too_large", max_bytes=self.configuration.max_scrape_bytes)
            projected: dict[str, JsonValue] = {
                "ok": True,
                "content": result.content,
                "source_url": result.source_url,
                "canonical_url": result.canonical_url,
                "title": result.title,
                "truncated": result.truncated,
                "bytes": len(encoded),
            }
            return cast(
                WebScrapeSuccess,
                await disclose_text_fields(
                    ctx.deps,
                    projected,
                    text_fields=("content",),
                    content_complete=not result.truncated,
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
                if not is_text_media_type(content_type):
                    return {
                        **_web_error("web_fetch_content_unsupported"),
                        "final_url": response.canonical_url,
                        "content_type": content_type or None,
                    }
                async with asyncio.timeout(_remaining_seconds(deadline)):
                    data, truncated = await _read_response_body(
                        response.body,
                        self.configuration.max_text_bytes,
                        max_chunk_bytes=self.configuration.stream_chunk_size,
                        truncate=True,
                    )
                text = data.decode(text_charset(_header(response.headers, "content-type")), errors="replace")
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
            return [environment_failure(exc)]

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
            return {"url": safe_requested, **environment_failure(exc)}
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
        domains = self.configuration.fetch if purpose == "fetch" else self.configuration.download
        policy = WebDomainPolicy(domains, attachment.policy) if domains.restricted else attachment.policy
        async with asyncio.timeout(_remaining_seconds(deadline)):
            await policy.authorize(url, purpose=purpose)
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
            response = await attachment.client.request(request, policy=policy)
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
                await policy.authorize(response.final_url, purpose=purpose)
        except BaseException:
            await _close_response(response)
            raise
        return response

    def _fetch_limit(self) -> int:
        return self.configuration.max_text_bytes


def _operation_deadline(seconds: float) -> float:
    return asyncio.get_running_loop().time() + seconds


def _remaining_seconds(deadline: float) -> float:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError
    return remaining


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
    _RESPONSE_CLOSE_TASKS.discard(task)
    try:
        task.exception()
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def _drain_response_close(response: WebResponse) -> None:
    close_task = asyncio.create_task(response.close(), name="web-response-close")
    _RESPONSE_CLOSE_TASKS.add(close_task)
    close_task.add_done_callback(_consume_close_task)
    done, _ = await asyncio.wait({close_task}, timeout=_CLOSE_GRACE_SECONDS)
    if close_task not in done:
        close_task.cancel()
        await asyncio.wait({close_task}, timeout=_CLOSE_GRACE_SECONDS)


async def _close_response(response: WebResponse) -> None:
    drain_task = asyncio.create_task(_drain_response_close(response), name="web-response-close-drain")
    _RESPONSE_CLOSE_TASKS.add(drain_task)
    drain_task.add_done_callback(_consume_close_task)
    await asyncio.shield(drain_task)


def _header(headers: Mapping[str, str], name: str) -> str:
    lowered = name.casefold()
    for key, value in headers.items():
        if key.casefold() == lowered:
            return value
    return ""


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
    result = tool_failure(
        "web_http_status",
        f"The server returned HTTP status {response.status_code}.",
        retry_hint="retry" if response.status_code >= 500 or response.status_code == 429 else "request_change",
    )
    result["error"]["status_code"] = response.status_code
    result["error"]["reason"] = response.reason or None
    return {
        **result,
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
    message = {
        "web_binding_missing": "No web client is configured for this Run.",
        "web_body_too_large": "The response body exceeds the configured byte limit.",
        "web_download_batch_too_large": "Too many download URLs were requested; use a smaller batch.",
        "web_download_failed": "The download failed; check the URL and destination.",
        "web_fetch_failed": "The web request failed; check the URL and network access.",
        "web_fetch_content_unsupported": "Fetch supports textual HTTP responses; use download or a suitable media or document tool for this content.",
        "web_redirect_invalid": "The server returned an invalid or disallowed redirect.",
        "web_domain_denied": "The requested domain is denied by Web configuration.",
        "web_response_invalid": "The web client returned an invalid response.",
        "web_scrape_backend_missing": "No scrape backend is configured.",
        "web_scrape_domain_restrictions_unsupported": "The selected scrape provider cannot enforce domain restrictions.",
        "web_scrape_failed": "The scrape provider could not read the page.",
        "web_scrape_response_invalid": "The scrape provider returned an invalid result.",
        "web_scrape_unavailable": "The configured scrape provider is unavailable.",
        "web_search_backend_missing": "No search backend is configured.",
        "web_search_failed": "The search provider could not complete the query.",
        "web_search_response_invalid": "The search provider returned an invalid result.",
        "web_search_unavailable": "The configured search provider is unavailable.",
        "web_timeout": "The web operation timed out.",
    }.get(code, "The web operation failed; check the request and configured provider.")
    result = tool_failure(code, message, retry_hint=retry_hint)
    if max_bytes is not None:
        result["error"]["max_bytes"] = max_bytes
    return result


__all__ = [
    "WEB_SCRAPE_BACKEND_ENV",
    "WEB_SCRAPE_BACKEND_PRIORITY_ENV",
    "WEB_SCRAPE_MODE_ENV",
    "WEB_SEARCH_BACKEND_ENV",
    "WEB_SEARCH_BACKEND_PRIORITY_ENV",
    "WEB_SEARCH_CONTEXT_SIZE_ENV",
    "WEB_SEARCH_MODE_ENV",
    "WebClient",
    "WebConfiguration",
    "WebDomainPolicy",
    "WebDownloadConfiguration",
    "WebDownloadItemResult",
    "WebDownloadToolResult",
    "WebFetchConfiguration",
    "WebFetchToolResult",
    "WebPolicy",
    "WebProviderError",
    "WebRequest",
    "WebResponse",
    "WebScrapeBackendBinding",
    "WebScrapeConfiguration",
    "WebScrapeProvider",
    "WebScrapeRequest",
    "WebScrapeResult",
    "WebScrapeToolResult",
    "WebSearchBackendBinding",
    "WebSearchConfiguration",
    "WebSearchProvider",
    "WebSearchRequest",
    "WebSearchResponse",
    "WebSearchResult",
    "WebSearchToolResult",
    "WebStatusFailure",
    "WebToolset",
]
