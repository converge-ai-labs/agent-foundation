"""Short-session Memory Provider eligibility and representation checks."""

from a13n_harness.memory_plugins import MemoryBackendCatalog
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
    catalog: MemoryBackendCatalog | None = None,
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


def require_eligible(record: MemoryProviderRecord, catalog: MemoryBackendCatalog) -> None:
    if not record.enabled:
        raise MemoryProviderError(
            "memory_provider_disabled", "Memory Provider is disabled.", category=ErrorCategory.conflict
        )
    if record.ciphertext is None:
        raise MemoryProviderError(
            "memory_provider_credential_missing",
            "Memory Provider requires a credential.",
            category=ErrorCategory.conflict,
        )
    if record.type not in catalog:
        raise MemoryProviderError(
            "memory_provider_unavailable",
            "Memory Provider implementation is unavailable.",
            category=ErrorCategory.unavailable,
        )


def require_document_support(provider_type: str, catalog: MemoryBackendCatalog | None) -> None:
    plugin = catalog.get(provider_type) if catalog is not None else None
    if plugin is None:
        raise MemoryProviderError(
            "memory_provider_unavailable",
            "Memory Provider implementation is unavailable.",
            category=ErrorCategory.unavailable,
        )
    if not plugin.supports_documents:
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
