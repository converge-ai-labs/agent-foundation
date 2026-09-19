"""Eligibility of optional resources selected by an invocation."""

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.memory import MemoryProviderDefinition
from a13n_harness.providers.web.definition import WebProviderDefinition
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_workspace
from a13n_service.memory.domain import memory_provider_ids
from a13n_service.memory.resources import require_memory_configuration
from a13n_service.web.domain import ScrapeSelection, provider_selections
from a13n_service.web.resources import require_operation
from a13n_service.web.resources import require_provider as require_web_provider

from ..domain import AgentConfig
from ..invocation import MergedAgentRunConfig
from ..toolsets import web_selection


async def validate_selected_resources(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    authored: AgentConfig | MergedAgentRunConfig,
    selected: MergedAgentRunConfig,
    memory_provider_catalog: ProviderCatalog[MemoryProviderDefinition],
    web_provider_catalog: ProviderCatalog[WebProviderDefinition],
) -> None:
    memory = selected.memory
    if memory is not None:
        if set(memory_provider_ids(memory)) - (set(memory_provider_ids(authored.memory)) if authored.memory else set()):
            await authorize_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.memory_provider_read
            )
        await require_memory_configuration(
            session,
            organization_id=organization_id,
            workspace_id=workspace_id,
            selection=memory,
            catalog=memory_provider_catalog,
        )
    original_by_operation = dict(provider_selections(web_selection(authored.toolsets)))
    for operation, selection in provider_selections(web_selection(selected.toolsets)):
        provider = await require_web_provider(
            session,
            organization_id=organization_id,
            workspace_id=workspace_id,
            provider_id=selection.provider_id,
            eligible=True,
            catalog=web_provider_catalog,
        )
        require_operation(
            provider,
            operation,
            web_provider_catalog,
            selection=selection if isinstance(selection, ScrapeSelection) else None,
        )
        original = original_by_operation.get(operation)
        if original is None or original.provider_id != selection.provider_id:
            await authorize_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.web_provider_read
            )
