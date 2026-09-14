"""Explicit saved-account probe with one dispatch and independent audit."""

from a13n_harness.capabilities.web import WebProviderError, WebSearchRequest
from anyio import fail_after

from a13n_service.application_errors import ErrorCategory
from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor, WorkspaceAction
from a13n_service.iam.resource_scope import authorize_scope
from a13n_service.storage import transaction

from .adapters import SearchTransport
from .domain import SearchProviderTestResult, SearchSelection
from .execution import AuthorizedSearch, SearchSnapshot
from .resources import SearchProviderError, require_eligible, require_provider
from .service import SearchProviderService


async def test_account(
    service: SearchProviderService,
    *,
    actor: AuthenticatedActor,
    workspace_id: str | None,
    provider_id: str,
    transport: SearchTransport,
) -> SearchProviderTestResult:
    expected_etag: str | None = None

    async def acquire() -> SearchSnapshot:
        nonlocal expected_etag
        async with transaction(service.sessions) as session:
            scope = await authorize_scope(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.search_provider_manage
            )
            record = await require_provider(
                session,
                organization_id=scope.organization_id,
                workspace_id=workspace_id,
                provider_id=provider_id,
            )
            current = resource_etag(record.id, record.updated_at)
            if expected_etag is not None and expected_etag != current:
                raise SearchProviderError(
                    "search_provider_changed",
                    "Search Provider changed during the test.",
                    category=ErrorCategory.conflict,
                )
            require_eligible(record)
            expected_etag = current
            return SearchSnapshot(record.id, record.type, record.credential_snapshot())

    async def reauthorize() -> None:
        await acquire()

    search = AuthorizedSearch(
        selection=SearchSelection(provider_id=provider_id, max_results=1),
        acquire=acquire,
        reauthorize=reauthorize,
        protector=service.protector,
        transport=transport,
        max_dispatches=1,
    )
    code: str | None = None
    try:
        await search.search(WebSearchRequest(query="Agent Foundation", limit=1))
    except WebProviderError as error:
        code = error.code
    except TimeoutError:
        code = "web_timeout"
    with fail_after(5):
        # Recheck even after uncertain transport failure before reporting or auditing.
        await reauthorize()
        async with transaction(service.sessions) as session:
            scope = await authorize_scope(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.search_provider_manage
            )
            record = await require_provider(
                session, organization_id=scope.organization_id, workspace_id=workspace_id, provider_id=provider_id
            )
            if expected_etag != resource_etag(record.id, record.updated_at):
                raise SearchProviderError(
                    "search_provider_changed",
                    "Search Provider changed during the test.",
                    category=ErrorCategory.conflict,
                )
            service.audit(session, actor, record, "test", code=code)
    return SearchProviderTestResult(success=code is None, code=code, checked_at=service.clock())
