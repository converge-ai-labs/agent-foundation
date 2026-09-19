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


@dataclass(frozen=True, slots=True)
class WebOperationPolicy:
    """The per-operation failure vocabulary of one Web dispatch."""

    unexpected_error_code: str
    retry_codes: frozenset[str]


_SEARCH = WebOperationPolicy(
    unexpected_error_code="web_search_unavailable",
    retry_codes=frozenset({"web_search_rate_limited", "web_search_unavailable"}),
)
_SCRAPE = WebOperationPolicy(
    unexpected_error_code="web_scrape_unavailable",
    retry_codes=frozenset({"web_scrape_rate_limited", "web_scrape_unavailable"}),
)


class WebDispatcher:
    """Re-read the authorized Provider snapshot before every attempt of one operation."""

    # Selection validation proves the concrete adapter can enforce restrictions.
    supports_domain_restrictions = True

    def __init__(
        self,
        *,
        selection: SearchSelection | ScrapeSelection,
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
        self._options: SearchOptions | ScrapeOptions
        if isinstance(selection, SearchSelection):
            self._options = SearchOptions(
                max_results=selection.max_results,
                allow_domains=selection.allow_domains,
                deny_domains=selection.deny_domains,
            )
            self._policy = _SEARCH
        else:
            self._options = ScrapeOptions(
                max_content_bytes=selection.max_content_bytes,
                allow_domains=selection.allow_domains,
                deny_domains=selection.deny_domains,
            )
            self._policy = _SCRAPE

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        return await self._dispatch(lambda runtime: runtime.search(request))

    async def scrape(self, request: WebScrapeRequest, *, policy: WebPolicy) -> WebScrapeResult:
        return await self._dispatch(lambda runtime: runtime.scrape(request, policy=policy))

    async def _dispatch[ResultT](self, operation: Callable[[WebProvider], Awaitable[ResultT]]) -> ResultT:
        deadline = current_time() + 30
        with fail_after(30):
            for attempt in range(self._max_dispatches):
                snapshot = await self._acquire()
                if snapshot.provider_id != self._selection.provider_id:
                    raise RuntimeError("Web Provider binding changed")
                try:
                    definition = self._catalog.require(snapshot.provider_type)
                    credential_value = (
                        json.loads(snapshot.credential.decrypt(self._protector))
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
                        configuration, credentials, options=self._options, transport=self._transport
                    ) as runtime:
                        result = await operation(runtime)
                except WebProviderError as error:
                    failure = error
                except Exception as error:
                    failure = WebProviderError(self._policy.unexpected_error_code)
                    failure.__cause__ = error
                finally:
                    del credential_value, credentials, configuration, snapshot
                await self._reauthorize()
                if failure is None:
                    assert result is not None
                    return result
                if (
                    isinstance(failure, WebProviderResponseError)
                    and failure.code in self._policy.retry_codes
                    and attempt + 1 < self._max_dispatches
                ):
                    delay = failure.retry_after_seconds if failure.retry_after_seconds is not None else 1.0
                    if delay < deadline - current_time():
                        await sleep(delay)
                        continue
                raise failure
        raise AssertionError("Web Provider dispatch exhausted without outcome")
