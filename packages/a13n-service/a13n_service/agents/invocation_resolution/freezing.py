"""Short-transaction freezing for prepared Agent invocations."""

from __future__ import annotations

from a13n_harness.memory_plugins import MemoryBackendCatalog
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.selection_resolution import (
    ConnectivitySelectionResolver,
)
from a13n_service.digests import digest_request
from a13n_service.iam import (
    AuthorizationError,
    WorkspaceAction,
    authorize_agent,
    authorize_workspace,
)
from a13n_service.memory.resources import require_provider as require_memory_provider
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.models.service import ModelError
from a13n_service.models.settings import effective_settings
from a13n_service.web.domain import ScrapeSelection, provider_selections
from a13n_service.web.registry import WebProviderRegistry
from a13n_service.web.resources import require_operation
from a13n_service.web.resources import require_provider as require_web_provider

from ..connectivity_resolution import freeze_invocation_connectivity
from ..domain import (
    AgentConfig,
    ChildAgentExecution,
    EffectiveAgentConfig,
    EffectiveAgentModel,
)
from ..errors import (
    agent_revision_not_executable,
    current_revision_conflict,
    map_authorization_error,
    map_model_error,
)
from ..model_characteristics import compose_model_characteristics
from ..toolsets import web_selection
from .contracts import (
    AgentSelectorKind,
    FrozenAgentInvocation,
    PreparedAgentInvocation,
)
from .queries import load_agent_record, load_revision_record, require_invocable_agent
from .skills import freeze_skills


class AgentInvocationFreezer:
    """Reauthorize and freeze prepared invocation evidence in one short transaction."""

    def __init__(
        self,
        model_selector: AcceptedModelSelector,
        *,
        connectivity_resolver: ConnectivitySelectionResolver,
        web_provider_registry: WebProviderRegistry,
        memory_backend_catalog: MemoryBackendCatalog,
    ) -> None:
        self._model_selector = model_selector
        self._connectivity_resolver = connectivity_resolver
        self._web_provider_registry = web_provider_registry
        self._memory_backend_catalog = memory_backend_catalog

    async def freeze_in_transaction(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedAgentInvocation,
    ) -> FrozenAgentInvocation:
        try:
            if prepared.configuration_context is not None:
                from a13n_service.agent_configuration.authorization import authorize_invocation

                await authorize_invocation(
                    session, actor=prepared.actor, agent_id=prepared.agent_id, context=prepared.configuration_context
                )
            else:
                await authorize_agent(
                    session,
                    actor=prepared.actor,
                    workspace_id=prepared.workspace_id,
                    agent_id=prepared.agent_id,
                    action=WorkspaceAction.agent_invoke,
                )
            agent = await load_agent_record(
                session,
                organization_id=prepared.organization_id,
                workspace_id=prepared.workspace_id,
                agent_id=prepared.agent_id,
                for_update=True,
            )
            require_invocable_agent(
                agent,
                policy=prepared.root_state_policy,
            )
            if (
                prepared.expected_current_revision_id is not None
                and agent.current_revision_id != prepared.expected_current_revision_id
            ):
                raise current_revision_conflict(agent.current_revision_id)
            if (
                prepared.selector_kind is AgentSelectorKind.current
                and agent.current_revision_id != prepared.agent_revision_id
            ):
                raise current_revision_conflict(agent.current_revision_id)
            revision_record = await load_revision_record(
                session,
                organization_id=prepared.organization_id,
                workspace_id=prepared.workspace_id,
                agent_id=prepared.agent_id,
                revision_id=prepared.agent_revision_id,
                for_update=True,
            )
            if revision_record.content_digest != prepared.revision_content_digest:
                raise agent_revision_not_executable("revision_changed")
            await authorize_workspace(
                session,
                actor=prepared.actor,
                workspace_id=prepared.workspace_id,
                action=WorkspaceAction.models_read,
            )
            try:
                execution = await self._model_selector.freeze_in_transaction(session, prepared=prepared.model)
                reviewer_execution = (
                    await self._model_selector.freeze_in_transaction(session, prepared=prepared.reviewer_model)
                    if prepared.reviewer_model is not None
                    else None
                )
            except ModelError as error:
                raise map_model_error(error) from error
            authored = AgentConfig.model_validate(revision_record.config)
            memory = prepared.merged.memory
            if memory is not None:
                if authored.memory is None or authored.memory.provider_id != memory.provider_id:
                    await authorize_workspace(
                        session,
                        actor=prepared.actor,
                        workspace_id=prepared.workspace_id,
                        action=WorkspaceAction.memory_provider_read,
                    )
                await require_memory_provider(
                    session,
                    organization_id=prepared.organization_id,
                    workspace_id=prepared.workspace_id,
                    provider_id=memory.provider_id,
                    eligible=True,
                    catalog=self._memory_backend_catalog,
                )
            original_web = web_selection(authored.toolsets)
            original_by_operation = dict(provider_selections(original_web))
            for operation, selection in provider_selections(web_selection(prepared.merged.toolsets)):
                provider = await require_web_provider(
                    session,
                    organization_id=prepared.organization_id,
                    workspace_id=prepared.workspace_id,
                    provider_id=selection.provider_id,
                    eligible=True,
                    registry=self._web_provider_registry,
                )
                require_operation(
                    provider,
                    operation,
                    self._web_provider_registry,
                    selection=selection if isinstance(selection, ScrapeSelection) else None,
                )
                original_operation = original_by_operation.get(operation)
                original_provider = original_operation.provider_id if original_operation is not None else None
                if original_provider != selection.provider_id:
                    await authorize_workspace(
                        session,
                        actor=prepared.actor,
                        workspace_id=prepared.workspace_id,
                        action=WorkspaceAction.web_provider_read,
                    )
            skills = await freeze_skills(session, prepared)
            connectivity = await freeze_invocation_connectivity(
                self._connectivity_resolver,
                session,
                prepared.connectivity,
            )
        except AuthorizationError as error:
            raise map_authorization_error(error) from error

        resolved_subagents = tuple(item.edge for item in prepared.subagents)
        child_configs = {}
        for item in prepared.subagents:
            child = item.invocation
            frozen_child = await self.freeze_in_transaction(session, prepared=child)
            child_configs[child.agent_revision_id] = ChildAgentExecution(
                agent_id=child.agent_id,
                revision_content_digest=child.revision_content_digest,
                effective_config=frozen_child.effective_config,
                connection_selections=frozen_child.connection_selections,
            )
        config_payload = {
            "subagent_mode": prepared.merged.subagent_mode,
            "child_configs": child_configs,
            "schema_version": "1",
            "resolved_model": EffectiveAgentModel(
                execution=execution,
                settings=effective_settings(
                    execution.model_api,
                    prepared.model.resource.settings,
                    *prepared.model.settings_layers,
                ),
                characteristics=compose_model_characteristics(
                    prepared.model.resource.declarations,
                    prepared.merged.model.characteristics,
                ),
            ),
            "toolsets": prepared.merged.toolsets,
            "reviewer": prepared.merged.reviewer,
            "resolved_reviewer_model": (
                EffectiveAgentModel(
                    execution=reviewer_execution,
                    settings=effective_settings(
                        reviewer_execution.model_api,
                        prepared.reviewer_model.resource.settings,
                        *prepared.reviewer_model.settings_layers,
                    ),
                    characteristics=compose_model_characteristics(prepared.reviewer_model.resource.declarations),
                )
                if reviewer_execution is not None
                and prepared.reviewer_model is not None
                and prepared.merged.reviewer is not None
                else None
            ),
            "plugins": prepared.merged.plugins,
            "skills": skills,
            "connection_tools": prepared.merged.connection_tools,
            "resolved_subagents": resolved_subagents,
            "instructions": prepared.merged.instructions,
            "input_adapter": prepared.merged.input_adapter,
            "client_tools": prepared.merged.client_tools,
            "output_spec": prepared.merged.output_spec,
            "retries": prepared.merged.retries,
            "secret_requirements": prepared.merged.secret_requirements,
            "memory": prepared.merged.memory,
            "protocol": prepared.merged.protocol,
        }
        effective_without_digest = EffectiveAgentConfig(
            **config_payload,
            content_digest="0" * 64,
        )
        digest_payload = effective_without_digest.model_dump(
            mode="json",
            by_alias=True,
            exclude={"content_digest"},
        )
        effective = EffectiveAgentConfig(
            **config_payload,
            content_digest=digest_request(digest_payload),
        )
        return FrozenAgentInvocation(
            agent_id=prepared.agent_id,
            agent_revision_id=prepared.agent_revision_id,
            selector_kind=prepared.selector_kind,
            effective_config=effective,
            connection_selections=connectivity.connection_selections,
        )
