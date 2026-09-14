"""Fresh encrypted snapshots at every dispatch and authorization before disclosure."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from a13n_harness.capabilities.web import WebProviderError, WebSearchRequest, WebSearchResponse
from anyio import current_time, fail_after, sleep

from a13n_service.credentials import CredentialSnapshot
from a13n_service.secrets.crypto import SecretProtectionError, SecretProtector

from .adapters import SearchResponseError, SearchTransport
from .domain import SearchSelection


@dataclass(frozen=True, slots=True)
class SearchSnapshot:
    provider_id: str
    provider_type: str
    credential: CredentialSnapshot = field(repr=False)


class AuthorizedSearch:
    def __init__(
        self,
        *,
        selection: SearchSelection,
        acquire: Callable[[], Awaitable[SearchSnapshot]],
        reauthorize: Callable[[], Awaitable[None]],
        protector: SecretProtector,
        transport: SearchTransport,
        max_dispatches: int = 2,
    ) -> None:
        self._selection = selection
        self._acquire = acquire
        self._reauthorize = reauthorize
        self._protector = protector
        self._transport = transport
        self._max_dispatches = max_dispatches

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        deadline = current_time() + 30
        with fail_after(30):
            for attempt in range(self._max_dispatches):
                snapshot = await self._acquire()
                if snapshot.provider_id != self._selection.provider_id:
                    raise RuntimeError("Search Provider binding changed")
                try:
                    credential = snapshot.credential.decrypt(self._protector)
                except SecretProtectionError as error:
                    raise WebProviderError("web_search_unavailable") from error
                failure: WebProviderError | None = None
                result: WebSearchResponse | None = None
                try:
                    result = await self._transport.dispatch(
                        snapshot.provider_type, credential, request, self._selection
                    )
                except WebProviderError as error:
                    failure = error
                finally:
                    del credential, snapshot
                await self._reauthorize()
                if failure is None:
                    assert result is not None
                    return result
                if (
                    isinstance(failure, SearchResponseError)
                    and failure.code in {"web_search_rate_limited", "web_search_unavailable"}
                    and attempt + 1 < self._max_dispatches
                ):
                    delay = failure.retry_after if failure.retry_after is not None else 1.0
                    if delay < deadline - current_time():
                        await sleep(delay)
                        continue
                raise failure
        raise AssertionError("Search dispatch exhausted without outcome")
