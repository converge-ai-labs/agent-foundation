"""Short-session Web Provider eligibility and representation checks."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.etags import etag_matches, resource_etag
from a13n_service.iam.resource_scope import visible_workspace

from .domain import ScrapeSelection
from .models import WebProviderRecord
from .registry import WebProviderRegistry


class WebProviderError(ApplicationError):
    """A safe account or selection failure."""


async def require_provider(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str | None,
    provider_id: str,
    eligible: bool = False,
    registry: WebProviderRegistry | None = None,
    owning_scope: bool = False,
    lock: bool = False,
) -> WebProviderRecord:
    query = select(WebProviderRecord).where(
        WebProviderRecord.id == provider_id,
        WebProviderRecord.organization_id == organization_id,
        WebProviderRecord.workspace_id == workspace_id
        if owning_scope
        else visible_workspace(WebProviderRecord.workspace_id, workspace_id),
    )
    record = await session.scalar(query.with_for_update() if lock else query)
    if record is None:
        raise WebProviderError("web_provider_not_found", "Web Provider not found.", category=ErrorCategory.not_found)
    if eligible:
        if registry is None:
            raise RuntimeError("Web Provider registry is required for eligibility")
        require_eligible(record, registry)
    return record


def require_eligible(record: WebProviderRecord, registry: WebProviderRegistry) -> None:
    if not record.enabled:
        raise WebProviderError("web_provider_disabled", "Web Provider is disabled.", category=ErrorCategory.conflict)
    try:
        definition = registry.require(record.type)
    except ValueError:
        raise WebProviderError(
            "web_provider_unavailable",
            "Web Provider implementation is unavailable.",
            category=ErrorCategory.unavailable,
        ) from None
    if (
        definition.authentication.resolve(definition.configuration_model.model_validate(record.configuration))
        == "required"
        and record.ciphertext is None
    ):
        raise WebProviderError(
            "web_provider_credential_missing",
            "Web Provider requires a credential.",
            category=ErrorCategory.conflict,
        )


def require_operation(
    record: WebProviderRecord,
    operation: str,
    registry: WebProviderRegistry,
    *,
    selection: ScrapeSelection | None = None,
) -> None:
    try:
        definition = registry.require(record.type)
    except ValueError:
        definition = None
    supported = definition is not None and (
        definition.supports_search if operation == "search" else definition.supports_scrape
    )
    if not supported:
        raise WebProviderError(
            "web_provider_operation_unsupported",
            f"Web Provider does not support {operation}.",
            category=ErrorCategory.conflict,
        )
    if operation == "scrape" and selection is not None and selection.restricted:
        assert definition is not None
        if not definition.supports_restricted_scrape:
            raise WebProviderError(
                "web_scrape_domain_restrictions_unsupported",
                "Web Provider cannot enforce scrape domain restrictions.",
                category=ErrorCategory.conflict,
            )


def require_etag(record: WebProviderRecord, if_match: str) -> None:
    current = resource_etag(record.id, record.updated_at)
    if not etag_matches(if_match, current):
        raise WebProviderError(
            "precondition_failed",
            "Web Provider changed after it was read.",
            category=ErrorCategory.stale_version,
            details={"current_etag": current},
        )
