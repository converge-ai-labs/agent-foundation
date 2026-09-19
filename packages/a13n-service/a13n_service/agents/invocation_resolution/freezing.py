"""Run snapshot composition and commit-time validation for Agent management."""

from __future__ import annotations

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.memory import MemoryProviderDefinition
from a13n_harness.providers.web.definition import WebProviderDefinition
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.selection_domain import ConnectionRunSelection
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
from a13n_service.models.domain import ModelExecutionSnapshot
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.models.service import ModelError
from a13n_service.models.settings import effective_settings
from a13n_service.skills.domain import SkillRevisionLock

from ..connectivity_resolution import freeze_invocation_connectivity
from ..domain import (
    AgentConfig,
    ChildAgentExecution,
    EffectiveAgentConfig,
    EffectiveAgentModel,
)
from ..errors import (
    agent_revision_not_executable,
    default_revision_conflict,
    map_authorization_error,
    map_model_error,
)
from ..model_characteristics import compose_model_characteristics
from .contracts import (
    AgentSelectorKind,
    FrozenAgentInvocation,
    PreparedAgentInvocation,
)
from .queries import load_agent_record, load_revision_record, require_invocable_agent
from .resources import validate_selected_resources
from .skills import freeze_skills


class AgentInvocationFreezer:
    """Compose Run snapshots or revalidate managed configuration before a management write."""

    def __init__(
        self,
        model_selector: AcceptedModelSelector,
        *,
        connectivity_resolver: ConnectivitySelectionResolver,
        web_provider_catalog: ProviderCatalog[WebProviderDefinition],
        memory_provider_catalog: ProviderCatalog[MemoryProviderDefinition],
    ) -> None:
        self._model_selector = model_selector
        self._connectivity_resolver = connectivity_resolver
        self._web_provider_catalog = web_provider_catalog
        self._memory_provider_catalog = memory_provider_catalog

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
                prepared.expected_default_revision_id is not None
                and agent.default_revision_id != prepared.expected_default_revision_id
            ):
                raise default_revision_conflict(agent.default_revision_id)
            if (
                prepared.selector_kind is AgentSelectorKind.current
                and agent.default_revision_id != prepared.agent_revision_id
            ):
                raise default_revision_conflict(agent.default_revision_id)
            if prepared.agent_revision_id is None:
                # This source is reachable only through protected configuration admission.
                authored = prepared.merged
            else:
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
                authored = AgentConfig.model_validate(revision_record.config)
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
            await validate_selected_resources(
                session,
                actor=prepared.actor,
                organization_id=prepared.organization_id,
                workspace_id=prepared.workspace_id,
                authored=authored,
                selected=prepared.merged,
                memory_provider_catalog=self._memory_provider_catalog,
                web_provider_catalog=self._web_provider_catalog,
            )
            skills = await freeze_skills(session, prepared)
            connectivity = await freeze_invocation_connectivity(
                self._connectivity_resolver,
                session,
                prepared.connectivity,
            )
        except AuthorizationError as error:
            raise map_authorization_error(error) from error

        child_configs = {}
        for item in prepared.subagents:
            child = item.invocation
            frozen_child = await self.freeze_in_transaction(session, prepared=child)
            assert child.agent_revision_id is not None and child.revision_content_digest is not None
            child_configs[child.agent_revision_id] = ChildAgentExecution(
                agent_id=child.agent_id,
                revision_content_digest=child.revision_content_digest,
                effective_config=frozen_child.effective_config,
                connection_selections=frozen_child.connection_selections,
            )
        return _compose_invocation(
            prepared,
            execution=execution,
            reviewer_execution=reviewer_execution,
            skills=skills,
            connection_selections=connectivity.connection_selections,
            child_configs=child_configs,
        )

    @staticmethod
    def freeze_selected(*, prepared: PreparedAgentInvocation) -> FrozenAgentInvocation:
        """Build one Run snapshot from checked selections, without querying current configuration."""
        child_configs = {}
        for item in prepared.subagents:
            child = item.invocation
            frozen = AgentInvocationFreezer.freeze_selected(prepared=child)
            assert child.agent_revision_id is not None and child.revision_content_digest is not None
            child_configs[child.agent_revision_id] = ChildAgentExecution(
                agent_id=child.agent_id,
                revision_content_digest=child.revision_content_digest,
                effective_config=frozen.effective_config,
                connection_selections=frozen.connection_selections,
            )
        return _compose_invocation(
            prepared,
            execution=ModelExecutionSnapshot.freeze(prepared.model.resource),
            reviewer_execution=ModelExecutionSnapshot.freeze(prepared.reviewer_model.resource)
            if prepared.reviewer_model is not None
            else None,
            skills=tuple(
                SkillRevisionLock(
                    skill_id=item.binding.skill_id,
                    skill_revision_id=item.revision_id,
                    skill_key=item.binding.skill_key,
                    version=item.revision_version,
                    content_digest=item.content_digest,
                )
                for item in prepared.skills
            ),
            connection_selections=prepared.connectivity.selections.connection_selections,
            child_configs=child_configs,
        )


def _compose_invocation(
    prepared: PreparedAgentInvocation,
    *,
    execution: ModelExecutionSnapshot,
    reviewer_execution: ModelExecutionSnapshot | None,
    skills: tuple[SkillRevisionLock, ...],
    connection_selections: tuple[ConnectionRunSelection, ...],
    child_configs: dict[str, ChildAgentExecution],
) -> FrozenAgentInvocation:
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
        "resolved_subagents": tuple(item.edge for item in prepared.subagents),
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
        connection_selections=connection_selections,
    )
