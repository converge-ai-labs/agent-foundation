import json
import math

import pytest
from a13n_harness.capabilities.web import (
    WebProviderError,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchRequest,
    WebSearchResponse,
)
from a13n_service.credentials import CredentialSnapshot
from a13n_service.provider_plugins import (
    WebProviderRegistration,
    WebProviderResponseError,
)
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.web.domain import ScrapeSelection, SearchSelection
from a13n_service.web.execution import AuthorizedScrape, AuthorizedSearch, WebProviderSnapshot
from a13n_service.web.registry import WebProviderRegistry
from pydantic import BaseModel, ConfigDict, SecretStr

pytestmark = pytest.mark.anyio

PROVIDER_ID = "wprov_1234567890abcdef"


class _Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key: SecretStr


class _Policy:
    async def authorize(self, _url: str, *, purpose: str) -> None:
        assert purpose == "scrape"


class _Runtime:
    def __init__(self, failures: list[BaseException | None]) -> None:
        self.failures = failures
        self.calls: list[str] = []
        self.closed = 0

    async def search(self, **_kwargs) -> WebSearchResponse:
        self._dispatch("search")
        return WebSearchResponse(results=())

    async def scrape(self, *, request, policy, **_kwargs) -> WebScrapeResult:
        await policy.authorize(request.url, purpose="scrape")
        self._dispatch("scrape")
        return WebScrapeResult(content="", source_url=request.url, canonical_url=request.url)

    def _dispatch(self, operation: str) -> None:
        self.calls.append(operation)
        failure = self.failures.pop(0)
        if failure is not None:
            raise failure

    async def aclose(self) -> None:
        self.closed += 1


def _boundary(operation: str, failures: list[BaseException | None]):
    protector = SecretProtector(key=b"k" * 32, encryption_key_id="test")
    encrypted = protector.encrypt(
        json.dumps({"api_key": "secret"}),
        secret_id=PROVIDER_ID,
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        owner_type="web_provider",
        owner_id=PROVIDER_ID,
        key="credential",
        version=1,
    )
    snapshot = WebProviderSnapshot(
        provider_id=PROVIDER_ID,
        provider_type="external_web",
        configuration={},
        credential=CredentialSnapshot(
            resource_id=PROVIDER_ID,
            owner_type="web_provider",
            owner_id=PROVIDER_ID,
            organization_id="org_1234567890abcdef",
            workspace_id="ws_1234567890abcdef",
            generation=1,
            ciphertext=encrypted.ciphertext,
            nonce=encrypted.nonce,
            encryption_key_id=encrypted.encryption_key_id,
        ),
    )
    runtime = _Runtime(failures)
    registry = WebProviderRegistry(
        (
            WebProviderRegistration(
                type="external_web",
                display_name="External Web",
                configuration_model=_Configuration,
                credential_model=_Credentials,
                setup_url="https://example.com/setup",
                factory=lambda: runtime,
                supports_search=operation == "search",
                supports_scrape=operation == "scrape",
            ),
        )
    )
    acquisitions = 0
    authorizations = 0

    async def acquire() -> WebProviderSnapshot:
        nonlocal acquisitions
        acquisitions += 1
        return snapshot

    async def reauthorize() -> None:
        nonlocal authorizations
        authorizations += 1

    if operation == "search":
        authorized = AuthorizedSearch(
            selection=SearchSelection(provider_id=PROVIDER_ID),
            acquire=acquire,
            reauthorize=reauthorize,
            protector=protector,
            registry=registry,
        )

        async def dispatch():
            return await authorized.search(WebSearchRequest(query="query", limit=1))

    else:
        authorized = AuthorizedScrape(
            selection=ScrapeSelection(provider_id=PROVIDER_ID),
            acquire=acquire,
            reauthorize=reauthorize,
            protector=protector,
            registry=registry,
        )

        async def dispatch():
            return await authorized.scrape(
                WebScrapeRequest(
                    url="https://example.com/",
                    max_content_bytes=1024,
                    deadline_seconds=30,
                    max_redirects=0,
                ),
                policy=_Policy(),
            )

    return dispatch, runtime, lambda: (acquisitions, authorizations)


async def test_credential_free_provider_dispatches_without_ciphertext() -> None:
    protector = SecretProtector(key=b"k" * 32, encryption_key_id="test")
    snapshot = WebProviderSnapshot(
        provider_id=PROVIDER_ID,
        provider_type="keyless_web",
        configuration={},
        credential=CredentialSnapshot(
            resource_id=PROVIDER_ID,
            owner_type="web_provider",
            owner_id=PROVIDER_ID,
            organization_id="org_1234567890abcdef",
            workspace_id="ws_1234567890abcdef",
            generation=0,
            ciphertext=None,
            nonce=None,
            encryption_key_id=None,
        ),
    )
    runtime = _Runtime([None])
    registry = WebProviderRegistry(
        (
            WebProviderRegistration(
                type="keyless_web",
                display_name="Keyless Web",
                configuration_model=_Configuration,
                credential_model=_Configuration,
                credential_required=False,
                setup_url="https://example.com/setup",
                factory=lambda: runtime,
                supports_search=True,
            ),
        )
    )

    async def acquire() -> WebProviderSnapshot:
        return snapshot

    async def reauthorize() -> None:
        pass

    authorized = AuthorizedSearch(
        selection=SearchSelection(provider_id=PROVIDER_ID),
        acquire=acquire,
        reauthorize=reauthorize,
        protector=protector,
        registry=registry,
    )
    result = await authorized.search(WebSearchRequest(query="query", limit=1))
    assert result.results == ()
    assert runtime.calls == ["search"] and runtime.closed == 1


@pytest.mark.parametrize("operation", ["search", "scrape"])
@pytest.mark.parametrize(
    "failure",
    [
        ConnectionError("response lost after dispatch"),
        RuntimeError("unexpected plugin failure"),
    ],
)
async def test_unknown_or_unexpected_failure_is_safe_and_never_replayed(operation, failure) -> None:
    dispatch, runtime, counts = _boundary(operation, [failure])
    with pytest.raises(WebProviderError) as caught:
        await dispatch()
    assert caught.value.code == f"web_{operation}_unavailable"
    assert "response lost" not in str(caught.value) and "unexpected plugin" not in str(caught.value)
    assert runtime.calls == [operation] and runtime.closed == 1
    assert counts() == (1, 1)


@pytest.mark.parametrize("operation", ["search", "scrape"])
@pytest.mark.parametrize("kind", ["rate_limited", "unavailable"])
async def test_explicit_retryable_response_permits_one_fresh_bounded_retry(operation, kind) -> None:
    code = f"web_{operation}_{kind}"
    dispatch, runtime, counts = _boundary(operation, [WebProviderResponseError(code, retry_after=0), None])
    await dispatch()
    assert runtime.calls == [operation, operation] and runtime.closed == 2
    assert counts() == (2, 2)


@pytest.mark.parametrize("operation", ["search", "scrape"])
@pytest.mark.parametrize("explicit", [False, True])
async def test_code_alone_or_nonretryable_response_cannot_authorize_replay(operation, explicit) -> None:
    code = f"web_{operation}_{'authentication_failed' if explicit else 'rate_limited'}"
    failure = WebProviderResponseError(code, retry_after=0) if explicit else WebProviderError(code)
    dispatch, runtime, counts = _boundary(operation, [failure])
    with pytest.raises(WebProviderError) as caught:
        await dispatch()
    assert caught.value.code == code
    assert runtime.calls == [operation] and runtime.closed == 1
    assert counts() == (1, 1)


@pytest.mark.parametrize("operation", ["search", "scrape"])
async def test_response_delay_outside_the_operation_budget_is_not_replayed(operation) -> None:
    code = f"web_{operation}_rate_limited"
    dispatch, runtime, counts = _boundary(
        operation,
        [WebProviderResponseError(code, retry_after=math.inf)],
    )
    with pytest.raises(WebProviderResponseError):
        await dispatch()
    assert runtime.calls == [operation] and runtime.closed == 1
    assert counts() == (1, 1)
