"""Explicit saved-account probe with one dispatch and independent audit."""

from a13n_harness.capabilities.web import WebProviderError as HarnessWebProviderError
from a13n_harness.capabilities.web import WebScrapeRequest, WebSearchRequest
from anyio import fail_after

from a13n_service.application_errors import ErrorCategory
from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor, WorkspaceAction
from a13n_service.iam.resource_scope import authorize_scope
from a13n_service.storage import transaction

from .adapters import WebProviderTransport
from .domain import ScrapeSelection, SearchSelection, WebProviderTestResult
from .execution import AuthorizedScrape, AuthorizedSearch, WebProviderSnapshot
from .registry import built_in_web_provider_registry
from .resources import WebProviderError, require_eligible, require_provider
from .service import WebProviderService


async def test_account(
    service: WebProviderService,
    *,
    actor: AuthenticatedActor,
    workspace_id: str | None,
    provider_id: str,
    transport: WebProviderTransport | None = None,
) -> WebProviderTestResult:
    expected_etag: str | None = None

    async def acquire() -> WebProviderSnapshot:
        nonlocal expected_etag
        async with transaction(service.sessions) as session:
            scope = await authorize_scope(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.web_provider_manage
            )
            record = await require_provider(
                session,
                organization_id=scope.organization_id,
                workspace_id=workspace_id,
                provider_id=provider_id,
            )
            current = resource_etag(record.id, record.updated_at)
            if expected_etag is not None and expected_etag != current:
                raise WebProviderError(
                    "web_provider_changed",
                    "Web Provider changed during the test.",
                    category=ErrorCategory.conflict,
                )
            require_eligible(record, service.registry)
            expected_etag = current
            return WebProviderSnapshot(record.id, record.type, record.configuration, record.credential_snapshot())

    initial = await acquire()
    pending: WebProviderSnapshot | None = initial

    async def acquire_for_dispatch() -> WebProviderSnapshot:
        nonlocal pending
        if pending is not None:
            snapshot, pending = pending, None
            return snapshot
        return await acquire()

    async def reauthorize() -> None:
        await acquire()

    registration = service.registry.require(initial.provider_type)
    registry = service.registry if transport is None else built_in_web_provider_registry(transport=transport)
    code: str | None = None
    try:
        if registration.supports_search:
            search = AuthorizedSearch(
                selection=SearchSelection(provider_id=provider_id, max_results=1),
                acquire=acquire_for_dispatch,
                reauthorize=reauthorize,
                protector=service.protector,
                registry=registry,
                max_dispatches=1,
            )
            await search.search(WebSearchRequest(query="Agent Foundation", limit=1))
        else:
            scrape = AuthorizedScrape(
                selection=ScrapeSelection(provider_id=provider_id, max_content_bytes=1024),
                acquire=acquire_for_dispatch,
                reauthorize=reauthorize,
                protector=service.protector,
                registry=registry,
                max_dispatches=1,
            )
            await scrape.scrape(
                WebScrapeRequest(
                    url="https://example.com/",
                    max_content_bytes=1024,
                    deadline_seconds=10,
                    max_redirects=0,
                ),
                policy=_ProbePolicy(),
            )
    except HarnessWebProviderError as error:
        code = error.code
    except TimeoutError:
        code = "web_timeout"
    with fail_after(5):
        # Recheck even after uncertain transport failure in the audit transaction.
        async with transaction(service.sessions) as session:
            scope = await authorize_scope(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.web_provider_manage
            )
            record = await require_provider(
                session, organization_id=scope.organization_id, workspace_id=workspace_id, provider_id=provider_id
            )
            if expected_etag != resource_etag(record.id, record.updated_at):
                raise WebProviderError(
                    "web_provider_changed",
                    "Web Provider changed during the test.",
                    category=ErrorCategory.conflict,
                )
            service.audit(session, actor, record, "test", code=code)
    return WebProviderTestResult(success=code is None, code=code, checked_at=service.clock())


class _ProbePolicy:
    async def authorize(self, url: str, *, purpose: str) -> None:
        if url != "https://example.com/" or purpose != "scrape":
            raise HarnessWebProviderError("web_domain_denied")
