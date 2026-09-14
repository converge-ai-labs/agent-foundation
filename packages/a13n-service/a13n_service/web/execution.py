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
from anyio import current_time, fail_after, sleep
from pydantic import BaseModel

from a13n_service.credentials import CredentialSnapshot
from a13n_service.provider_plugins.api import WebProviderRuntime
from a13n_service.secrets.crypto import SecretProtectionError, SecretProtector

from .domain import ScrapeSelection, SearchSelection
from .registry import WebProviderRegistry


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
    registry: WebProviderRegistry,
    operation: Callable[[WebProviderRuntime, BaseModel, BaseModel], Awaitable[ResultT]],
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
                credential_value = json.loads(snapshot.credential.decrypt(protector))
                credentials = registry.validate_credentials(snapshot.provider_type, credential_value)
                configuration = registry.require(snapshot.provider_type).configuration_model.model_validate(
                    snapshot.configuration
                )
            except (SecretProtectionError, ValueError, TypeError, json.JSONDecodeError) as error:
                raise WebProviderError("web_provider_unavailable") from error
            failure: WebProviderError | None = None
            result: ResultT | None = None
            try:
                async with registry.runtime(snapshot.provider_type) as runtime:
                    result = await operation(runtime, configuration, credentials)
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
            if failure.code in retry_codes and attempt + 1 < max_dispatches:
                retry_after = getattr(failure, "retry_after", None)
                delay = retry_after if isinstance(retry_after, int | float) else 1.0
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
        registry: WebProviderRegistry,
        max_dispatches: int = 2,
    ) -> None:
        self._selection = selection
        self._acquire = acquire
        self._reauthorize = reauthorize
        self._protector = protector
        self._registry = registry
        self._max_dispatches = max_dispatches

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        return await _dispatch(
            provider_id=self._selection.provider_id,
            acquire=self._acquire,
            reauthorize=self._reauthorize,
            protector=self._protector,
            registry=self._registry,
            operation=lambda runtime, configuration, credentials: runtime.search(
                configuration=configuration,
                credentials=credentials,
                request=request,
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
        registry: WebProviderRegistry,
        max_dispatches: int = 2,
    ) -> None:
        self._selection = selection
        self._acquire = acquire
        self._reauthorize = reauthorize
        self._protector = protector
        self._registry = registry
        self._max_dispatches = max_dispatches

    async def scrape(self, request: WebScrapeRequest, *, policy: WebPolicy) -> WebScrapeResult:
        return await _dispatch(
            provider_id=self._selection.provider_id,
            acquire=self._acquire,
            reauthorize=self._reauthorize,
            protector=self._protector,
            registry=self._registry,
            operation=lambda runtime, configuration, credentials: runtime.scrape(
                configuration=configuration,
                credentials=credentials,
                request=request,
                policy=policy,
                max_content_bytes=self._selection.max_content_bytes,
            ),
            unexpected_error_code="web_scrape_unavailable",
            retry_codes=frozenset({"web_scrape_rate_limited", "web_scrape_unavailable"}),
            max_dispatches=self._max_dispatches,
        )
