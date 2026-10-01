"""The web tools of a run's agent: provider backends for search and scrape, the host transport for the rest.

The web providers the selection names are resolved in the plan's short session under the run's authority and
opened outside it, like connections. Every provider dispatch is paid: it passes the attempt's call check first,
identified by the tool call it serves, and a refusal ends the run instead of reaching the model as a failed
search. Fetch and download need no provider: they use the host HTTP transport, whose endpoint policy also
checks every address a host resolves to when it connects.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx2
from a13n_harness import RunError
from a13n_harness.capabilities.web import (
    WebBinding,
    WebPolicy,
    WebProviderError,
    WebRequest,
    WebResponse,
    WebScrapeBackendBinding,
    WebScrapeProvider,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchBackendBinding,
    WebSearchProvider,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)
from a13n_harness.configuration import RunConfiguration
from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_harness.providers.web.contracts import WebPurpose
from a13n_harness.providers.web.options import ScrapeOptions, SearchOptions
from a13n_harness.tools import current_invocation_scope
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import ServiceError, invalid
from a13n_service.infra.outbound import open_http
from a13n_service.resources.agents.toolsets import WebTools
from a13n_service.resources.providers.service import ResolvedProvider, resolve_provider
from a13n_service.resources.providers.tables import WebProviderRow
from a13n_service.resources.web_providers.runtime import open_scrape_backend, open_search_backend
from a13n_service.runs.calls import CallCheck, Refused
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope

_REDIRECTS = frozenset({301, 302, 303, 307, 308})


@dataclass(frozen=True, slots=True)
class ResolvedWeb:
    """The provider serving each of the agent's search and scrape tools, with its operation bounds."""

    search: tuple[ResolvedProvider, SearchOptions] | None
    scrape: tuple[ResolvedProvider, ScrapeOptions] | None


async def resolve_web(
    session: AsyncSession,
    actor: Principal,
    scope: WorkspaceScope,
    tools: WebTools | None,
    *,
    authority: ExecutionAuthority,
) -> ResolvedWeb | None:
    """The selected web providers, enabled and usable under the run's authority; no external I/O."""
    if tools is None:
        return None
    search, scrape = None, None
    if (selected := tools.search) is not None:
        search = (
            await _provider(session, actor, scope, selected.provider_id, authority),
            SearchOptions(
                max_results=selected.max_results,
                allow_domains=selected.allow_domains,
                deny_domains=selected.deny_domains,
            ),
        )
    if (selected := tools.scrape) is not None:
        scrape = (
            await _provider(session, actor, scope, selected.provider_id, authority),
            ScrapeOptions(
                max_content_bytes=selected.max_content_bytes,
                allow_domains=selected.allow_domains,
                deny_domains=selected.deny_domains,
            ),
        )
    return ResolvedWeb(search, scrape)


async def _provider(
    session: AsyncSession,
    actor: Principal,
    scope: WorkspaceScope,
    provider_id: str | None,
    authority: ExecutionAuthority,
) -> ResolvedProvider:
    # Revision validation requires the provider; a stored selection without one is refused, never guessed.
    if provider_id is None:
        raise invalid("toolsets", "web search and scrape need a web provider")
    return await resolve_provider(session, actor, WebProviderRow, scope, provider_id, authority=authority)


@asynccontextmanager
async def open_web(
    web: ResolvedWeb, check: CallCheck, *, runtime: Runtime, configuration: RunConfiguration
) -> AsyncIterator[WebBinding]:
    """The run's `RunBindings.web`; provider handles stay open until the context exits."""
    policy = runtime.endpoint_policy.for_run(configuration)
    async with AsyncExitStack() as stack:
        searches: tuple[WebSearchBackendBinding, ...] = ()
        scrapes: tuple[WebScrapeBackendBinding, ...] = ()
        if web.search is not None:
            provider, options = web.search
            opened = await stack.enter_async_context(
                open_search_backend(provider, options, registry=runtime.registry, keys=runtime.keys, policy=policy)
            )
            searches = (
                WebSearchBackendBinding(opened.backend_id, _CheckedSearch(opened.provider, provider.id, check)),
            )
        if web.scrape is not None:
            provider, options = web.scrape
            opened = await stack.enter_async_context(
                open_scrape_backend(provider, options, registry=runtime.registry, keys=runtime.keys, policy=policy)
            )
            checked = _CheckedScrape(opened.provider, provider.id, check)
            scrapes = (WebScrapeBackendBinding(opened.backend_id, checked, opened.supports_domain_restrictions),)
        yield WebBinding(
            client=_HostTransport(policy, runtime.settings.providers.response_bytes),
            policy=_EndpointGuard(policy),
            search_backends=searches,
            scrape_backends=scrapes,
        )


async def _admit(check: CallCheck, source: str, provider_id: str) -> None:
    """Pass the call check for the tool call being served, before its provider is called."""
    try:
        invocation = current_invocation_scope().invocation
    except RuntimeError:
        raise RunError("A web provider call has no tool call identity", code="call_unidentified") from None
    try:
        await check.tool_call(
            call_id=invocation.tool_call_id, source=source, tool_name=invocation.tool_name, provider_id=provider_id
        )
    except Refused as error:
        # The web tools report provider errors to the model; only a Harness run error ends the run.
        raise RunError(str(error), code="call_refused") from error


@dataclass(frozen=True, slots=True)
class _CheckedSearch:
    provider: WebSearchProvider
    provider_id: str
    check: CallCheck

    async def search(self, request: WebSearchRequest) -> WebSearchResponse | Sequence[WebSearchResult]:
        await _admit(self.check, "web.search", self.provider_id)
        return await self.provider.search(request)


@dataclass(frozen=True, slots=True)
class _CheckedScrape:
    provider: WebScrapeProvider
    provider_id: str
    check: CallCheck

    async def scrape(self, request: WebScrapeRequest, *, policy: WebPolicy) -> WebScrapeResult:
        await _admit(self.check, "web.scrape", self.provider_id)
        return await self.provider.scrape(request, policy=policy)


@dataclass(frozen=True, slots=True)
class _EndpointGuard:
    """Refuses early what the operator endpoint policy refuses by scheme, name or literal address.

    Resolved addresses are checked by the host transport when it connects, so a DNS answer cannot bypass it.
    """

    endpoints: EndpointPolicy

    async def authorize(self, url: str, *, purpose: WebPurpose) -> None:
        try:
            await self.endpoints.validate(url)
        except EndpointPolicyError:
            raise WebProviderError("web_destination_denied") from None


@dataclass(frozen=True, slots=True)
class _HostTransport:
    """One bounded HTTP client per request, so no cookie or connection outlives it; redirects are followed hop by
    hop, each authorized by `policy` and the operator endpoint policy before it is sent."""

    endpoints: EndpointPolicy
    max_bytes: int

    async def request(self, request: WebRequest, *, policy: WebPolicy) -> WebResponse:
        stack = AsyncExitStack()
        try:
            client = await stack.enter_async_context(
                open_http(self.endpoints, timeout=request.deadline_seconds, max_bytes=self.max_bytes)
            )
            url, redirects = request.url, 0
            while True:
                await policy.authorize(url, purpose=request.purpose)
                response = await client.send(client.build_request(request.method, url), stream=True)
                if response.status_code not in _REDIRECTS:
                    stack.push_async_callback(response.aclose)
                    return WebResponse(
                        status_code=response.status_code,
                        final_url=url,
                        canonical_url=url,
                        headers=dict(response.headers),
                        body=_body(response, request.max_stream_chunk_bytes),
                        reason=response.reason_phrase or None,
                        redirect_count=redirects,
                        _close=stack.aclose,
                    )
                await response.aclose()
                location = response.headers.get("location")
                if location is None:
                    raise WebProviderError("web_redirect_invalid")
                if redirects == request.max_redirects:
                    raise WebProviderError("web_redirect_limit")
                target = urljoin(url, location)
                await self.endpoints.validate_redirect(url, target)
                url, redirects = target, redirects + 1
        except EndpointPolicyError:
            await stack.aclose()
            raise WebProviderError("web_destination_denied") from None
        except BaseException:
            await stack.aclose()
            raise


async def _body(response: httpx2.Response, chunk_bytes: int) -> AsyncIterator[bytes]:
    try:
        async for chunk in response.aiter_bytes(chunk_bytes):
            yield chunk
    except ServiceError as error:
        # The transport's own bound, or a compressed body it never decodes.
        raise WebProviderError(
            "web_body_too_large" if error.code == "payload_too_large" else "web_response_invalid"
        ) from None
