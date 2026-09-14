"""Short-session Search Provider eligibility and representation checks."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.etags import etag_matches, resource_etag
from a13n_service.iam.resource_scope import visible_workspace

from .models import SearchProviderRecord


class SearchProviderError(ApplicationError):
    """A safe account or selection failure."""


async def require_provider(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str | None,
    provider_id: str,
    eligible: bool = False,
    owning_scope: bool = False,
    lock: bool = False,
) -> SearchProviderRecord:
    query = select(SearchProviderRecord).where(
        SearchProviderRecord.id == provider_id,
        SearchProviderRecord.organization_id == organization_id,
        SearchProviderRecord.workspace_id == workspace_id
        if owning_scope
        else visible_workspace(SearchProviderRecord.workspace_id, workspace_id),
    )
    record = await session.scalar(query.with_for_update() if lock else query)
    if record is None:
        raise SearchProviderError(
            "search_provider_not_found", "Search Provider not found.", category=ErrorCategory.not_found
        )
    if eligible:
        require_eligible(record)
    return record


def require_eligible(record: SearchProviderRecord) -> None:
    if not record.enabled:
        raise SearchProviderError(
            "search_provider_disabled", "Search Provider is disabled.", category=ErrorCategory.conflict
        )
    if record.ciphertext is None:
        raise SearchProviderError(
            "search_provider_credential_missing",
            "Search Provider requires a credential.",
            category=ErrorCategory.conflict,
        )
    if record.type not in {"brave", "exa"}:
        raise SearchProviderError(
            "search_provider_unavailable",
            "Search Provider implementation is unavailable.",
            category=ErrorCategory.unavailable,
        )


def require_etag(record: SearchProviderRecord, if_match: str) -> None:
    current = resource_etag(record.id, record.updated_at)
    if not etag_matches(if_match, current):
        raise SearchProviderError(
            "precondition_failed",
            "Search Provider changed after it was read.",
            category=ErrorCategory.stale_version,
            details={"current_etag": current},
        )
