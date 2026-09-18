"""Short-session Memory Provider eligibility and representation checks."""

from a13n_harness.providers.memory import MemoryProviderCatalog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.etags import etag_matches, resource_etag
from a13n_service.iam.resource_scope import visible_workspace

from .models import MemoryProviderRecord


class MemoryProviderError(ApplicationError):
    """A safe Provider or selection failure."""


async def require_provider(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str | None,
    provider_id: str,
    catalog: MemoryProviderCatalog | None = None,
    eligible: bool = False,
    owning_scope: bool = False,
    lock: bool = False,
) -> MemoryProviderRecord:
    query = select(MemoryProviderRecord).where(
        MemoryProviderRecord.id == provider_id,
        MemoryProviderRecord.organization_id == organization_id,
        MemoryProviderRecord.workspace_id == workspace_id
        if owning_scope
        else visible_workspace(MemoryProviderRecord.workspace_id, workspace_id),
    )
    record = await session.scalar(query.with_for_update() if lock else query)
    if record is None:
        raise MemoryProviderError(
            "memory_provider_not_found", "Memory Provider not found.", category=ErrorCategory.not_found
        )
    if eligible:
        if catalog is None:
            raise TypeError("Eligibility requires the selected Memory Backend catalog")
        require_eligible(record, catalog)
    return record


def require_eligible(record: MemoryProviderRecord, catalog: MemoryProviderCatalog) -> None:
    if not record.enabled:
        raise MemoryProviderError(
            "memory_provider_disabled", "Memory Provider is disabled.", category=ErrorCategory.conflict
        )
    definition = catalog.get(record.type)
    if definition is None:
        raise MemoryProviderError(
            "memory_provider_unavailable",
            "Memory Provider implementation is unavailable.",
            category=ErrorCategory.unavailable,
        )
    try:
        configuration = definition.configuration_model.model_validate(record.configuration)
        definition.authentication.validate_presence(configuration, record.ciphertext is not None)
    except ValueError as error:
        raise MemoryProviderError(
            "memory_provider_credential_missing"
            if record.ciphertext is None
            else "memory_provider_configuration_invalid",
            "Memory Provider authentication or configuration is unavailable.",
            category=ErrorCategory.conflict,
        ) from error


def require_document_support(provider_type: str, catalog: MemoryProviderCatalog | None) -> None:
    definition = catalog.get(provider_type) if catalog is not None else None
    if definition is None:
        raise MemoryProviderError(
            "memory_provider_unavailable",
            "Memory Provider implementation is unavailable.",
            category=ErrorCategory.unavailable,
        )
    if not definition.supports_documents:
        raise MemoryProviderError(
            "memory_documents_unsupported",
            "This Memory Provider does not support Bot documents.",
            category=ErrorCategory.conflict,
        )


def require_etag(record: MemoryProviderRecord, if_match: str) -> None:
    current = resource_etag(record.id, record.updated_at)
    if not etag_matches(if_match, current):
        raise MemoryProviderError(
            "precondition_failed",
            "Memory Provider changed after it was read.",
            category=ErrorCategory.stale_version,
            details={"current_etag": current},
        )
