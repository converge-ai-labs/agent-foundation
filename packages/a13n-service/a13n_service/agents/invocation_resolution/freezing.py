"""Short-transaction freezing for prepared Agent invocations."""

from __future__ import annotations

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
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.models.service import ModelError
from a13n_service.models.settings import effective_settings

from ..connectivity_resolution import freeze_invocation_connectivity
from ..domain import (
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
    ) -> None:
        self._model_selector = model_selector
        self._connectivity_resolver = connectivity_resolver

    async def freeze_in_transaction(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedAgentInvocation,
    ) -> FrozenAgentInvocation:
        try:
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
            except ModelError as error:
                raise map_model_error(error) from error
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
                connector_connection_selections=frozen_child.connector_connection_selections,
                mcp_connection_selections=frozen_child.mcp_connection_selections,
            )
        config_payload = {
            "subagent_mode": prepared.merged.subagent_mode,
            "child_configs": child_configs,
            "schema_version": "1",
            "resolved_model": EffectiveAgentModel(
                execution=execution,
                settings=effective_settings(
                    execution.model_api, prepared.model.resource.settings, prepared.merged.model.settings
                ),
                characteristics=prepared.merged.model.characteristics,
            ),
            "plugins": prepared.merged.plugins,
            "skills": skills,
            "connector_tools": prepared.merged.connector_tools,
            "mcp_tools": prepared.merged.mcp_tools,
            "resolved_subagents": resolved_subagents,
            "instructions": prepared.merged.instructions,
            "input_adapter": prepared.merged.input_adapter,
            "client_tools": prepared.merged.client_tools,
            "output_spec": prepared.merged.output_spec,
            "retries": prepared.merged.retries,
            "secret_requirements": prepared.merged.secret_requirements,
            "asset_publication": prepared.merged.asset_publication,
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
            connector_connection_selections=connectivity.connector_connection_selections,
            mcp_connection_selections=connectivity.mcp_connection_selections,
        )
