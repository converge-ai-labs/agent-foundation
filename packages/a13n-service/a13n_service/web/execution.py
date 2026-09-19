"""Fresh encrypted Web Provider snapshots at every operation dispatch."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from a13n_harness.capabilities.web import (
    WebPolicy,
    WebProviderError,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchRequest,
    WebSearchResponse,
)
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.web.definition import WebProvider, WebProviderDefinition
from a13n_harness.providers.web.errors import WebProviderResponseError
from a13n_harness.providers.web.options import ScrapeOptions, SearchOptions
from a13n_harness.providers.web.transport import WebProviderTransport
from anyio import current_time, fail_after, sleep

from a13n_service.credentials import CredentialSnapshot
from a13n_service.secrets.crypto import SecretProtectionError, SecretProtector

from .domain import ScrapeSelection, SearchSelection


@dataclass(frozen=True, slots=True)
class WebProviderSnapshot:
    provider_id: str
    provider_type: str
    configuration: dict[str, object]
    credential: CredentialSnapshot = field(repr=False)


async def _dispatch[ResultT](
    *,
    provider_id: str,
    acquire: Callable[[], Awaitable[WebProviderSnapshot]],
    reauthorize: Callable[[], Awaitable[None]],
    protector: SecretProtector,
    catalog: ProviderCatalog[WebProviderDefinition],
    transport: WebProviderTransport | None,
    operation: Callable[[WebProvider], Awaitable[ResultT]],
    search_options: SearchOptions | None = None,
    scrape_options: ScrapeOptions | None = None,
    unexpected_error_code: str,
    retry_codes: frozenset[str],
    max_dispatches: int,
) -> ResultT:
    deadline = current_time() + 30
    with fail_after(30):
        for attempt in range(max_dispatches):
            snapshot = await acquire()
            if snapshot.provider_id != provider_id:
                raise RuntimeError("Web Provider binding changed")
            try:
                definition = catalog.require(snapshot.provider_type)
                credential_value = (
                    json.loads(snapshot.credential.decrypt(protector))
                    if snapshot.credential.ciphertext is not None
                    else None
                )
                configuration = definition.configuration_model.model_validate(snapshot.configuration)
                credentials = definition.parse_credential(configuration, credential_value)
            except (SecretProtectionError, ValueError, TypeError, json.JSONDecodeError) as error:
                raise WebProviderError("web_provider_unavailable") from error
            failure: WebProviderError | None = None
            result: ResultT | None = None
            try:
                async with definition.open(
                    configuration,
                    credentials,
                    search_options=search_options,
                    scrape_options=scrape_options,
                    transport=transport,
                ) as runtime:
                    result = await operation(runtime)
            except WebProviderError as error:
                failure = error
            except Exception as error:
                failure = WebProviderError(unexpected_error_code)
                failure.__cause__ = error
            finally:
                del credential_value, credentials, configuration, snapshot
            await reauthorize()
            if failure is None:
                assert result is not None
                return result
            if (
                isinstance(failure, WebProviderResponseError)
                and failure.code in retry_codes
                and attempt + 1 < max_dispatches
            ):
                delay = failure.retry_after if failure.retry_after is not None else 1.0
                if delay < deadline - current_time():
                    await sleep(delay)
                    continue
            raise failure
    raise AssertionError("Web Provider dispatch exhausted without outcome")


class AuthorizedSearch:
    def __init__(
        self,
        *,
        selection: SearchSelection,
        acquire: Callable[[], Awaitable[WebProviderSnapshot]],
        reauthorize: Callable[[], Awaitable[None]],
        protector: SecretProtector,
        catalog: ProviderCatalog[WebProviderDefinition],
        transport: WebProviderTransport | None = None,
        max_dispatches: int = 2,
    ) -> None:
        self._selection = selection
        self._acquire = acquire
        self._reauthorize = reauthorize
        self._protector = protector
        self._catalog = catalog
        self._transport = transport
        self._max_dispatches = max_dispatches

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        return await _dispatch(
            provider_id=self._selection.provider_id,
            acquire=self._acquire,
            reauthorize=self._reauthorize,
            protector=self._protector,
            catalog=self._catalog,
            transport=self._transport,
            operation=lambda runtime: runtime.search(request),
            search_options=SearchOptions(
                max_results=self._selection.max_results,
                allow_domains=self._selection.allow_domains,
                deny_domains=self._selection.deny_domains,
            ),
            unexpected_error_code="web_search_unavailable",
            retry_codes=frozenset({"web_search_rate_limited", "web_search_unavailable"}),
            max_dispatches=self._max_dispatches,
        )


class AuthorizedScrape:
    # Selection validation proves the concrete adapter can enforce restrictions.
    supports_domain_restrictions = True

    def __init__(
        self,
        *,
        selection: ScrapeSelection,
        acquire: Callable[[], Awaitable[WebProviderSnapshot]],
        reauthorize: Callable[[], Awaitable[None]],
        protector: SecretProtector,
        catalog: ProviderCatalog[WebProviderDefinition],
        transport: WebProviderTransport | None = None,
        max_dispatches: int = 2,
    ) -> None:
        self._selection = selection
        self._acquire = acquire
        self._reauthorize = reauthorize
        self._protector = protector
        self._catalog = catalog
        self._transport = transport
        self._max_dispatches = max_dispatches

    async def scrape(self, request: WebScrapeRequest, *, policy: WebPolicy) -> WebScrapeResult:
        return await _dispatch(
            provider_id=self._selection.provider_id,
            acquire=self._acquire,
            reauthorize=self._reauthorize,
            protector=self._protector,
            catalog=self._catalog,
            transport=self._transport,
            operation=lambda runtime: runtime.scrape(request, policy=policy),
            scrape_options=ScrapeOptions(
                max_content_bytes=self._selection.max_content_bytes,
                allow_domains=self._selection.allow_domains,
                deny_domains=self._selection.deny_domains,
            ),
            unexpected_error_code="web_scrape_unavailable",
            retry_codes=frozenset({"web_scrape_rate_limited", "web_scrape_unavailable"}),
            max_dispatches=self._max_dispatches,
        )
