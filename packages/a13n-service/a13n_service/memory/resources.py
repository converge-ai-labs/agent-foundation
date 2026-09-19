"""Short-session Memory Provider eligibility and representation checks."""

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.memory import MemoryProviderDefinition
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.etags import etag_matches, resource_etag
from a13n_service.iam.resource_scope import visible_workspace

from .domain import InlineMemoryBackend, MemoryConfiguration, MemoryEntries, memory_provider_ids
from .models import MemoryProviderRecord


class MemoryProviderError(ApplicationError):
    """A safe Provider or selection failure."""


async def require_provider(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str | None,
    provider_id: str,
    catalog: ProviderCatalog[MemoryProviderDefinition] | None = None,
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


def require_eligible(record: MemoryProviderRecord, catalog: ProviderCatalog[MemoryProviderDefinition]) -> None:
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


def supports_document_entries(definition: MemoryProviderDefinition) -> bool:
    """Document entries bind Host-owned files, so their Provider opens no record backend."""

    return definition.supports_documents and not definition.supports_records


def require_document_support(provider_type: str, catalog: ProviderCatalog[MemoryProviderDefinition] | None) -> None:
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


async def require_memory_configuration(
    session: AsyncSession,
    *,
    selection: MemoryConfiguration,
    organization_id: str,
    workspace_id: str,
    catalog: ProviderCatalog[MemoryProviderDefinition],
) -> None:
    providers = {}
    for provider_id in memory_provider_ids(selection):
        providers[provider_id] = await require_provider(
            session,
            organization_id=organization_id,
            workspace_id=workspace_id,
            provider_id=provider_id,
            eligible=True,
            catalog=catalog,
        )
    if isinstance(selection, MemoryEntries):
        for entry in selection.entries:
            backend = entry.backend
            key = backend.type if isinstance(backend, InlineMemoryBackend) else providers[backend.provider_id].type
            definition = catalog.get(key)
            supported = definition is not None and (
                definition.supports_records if entry.mode == "records" else supports_document_entries(definition)
            )
            if not supported:
                raise MemoryProviderError(
                    "memory_mode_unsupported",
                    "The selected backend is unavailable for this memory mode.",
                    category=ErrorCategory.invalid_request,
                )
            if isinstance(backend, InlineMemoryBackend):
                assert definition is not None
                try:
                    definition.configuration_model.model_validate(backend.configuration)
                except ValueError as error:
                    raise MemoryProviderError(
                        "memory_backend_configuration_invalid",
                        "The inline memory backend configuration is invalid.",
                        category=ErrorCategory.invalid_request,
                    ) from error
    elif not catalog[providers[selection.provider_id].type].supports_records:
        raise MemoryProviderError(
            "memory_mode_unsupported",
            "This backend requires a documents entry.",
            category=ErrorCategory.invalid_request,
        )
