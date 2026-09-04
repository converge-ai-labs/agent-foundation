"""Short-transaction freezing for prepared Agent invocations."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.selection_resolution import (
    ConnectivitySelectionResolver,
)
from a13n_service.iam import (
    AuthorizationError,
    WorkspaceAction,
    authorize_agent,
    authorize_workspace,
)
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.models.service import ModelError
from a13n_service.models.settings import effective_settings
from a13n_service.plugins.runtime import PluginRuntimeLockError

from ..connectivity_resolution import freeze_invocation_connectivity
from ..domain import (
    EffectiveAgentConfig,
    EffectiveAgentModel,
    PluginRuntimeMode,
    canonical_digest,
)
from ..errors import (
    agent_revision_not_executable,
    current_revision_conflict,
    map_authorization_error,
    map_model_error,
)
from ..plugin_resolution import AgentPluginSelectionResolver, PluginSelectionError
from .contracts import (
    AgentSelectorKind,
    FrozenAgentInvocation,
    PreparedAgentInvocation,
    PreparedAgentRevisionGraph,
    RootAgentStatePolicy,
)
from .graph import freeze_subagents, load_agent_record, load_revision_record, require_invocable_agent
from .signatures import runtime_selection_unchanged
from .skills import freeze_skills


class AgentInvocationFreezer:
    """Reauthorize and freeze prepared invocation evidence in one short transaction."""

    def __init__(
        self,
        model_selector: AcceptedModelSelector,
        *,
        plugin_runtime_mode: PluginRuntimeMode,
        plugin_resolver: AgentPluginSelectionResolver,
        connectivity_resolver: ConnectivitySelectionResolver | None,
    ) -> None:
        self._model_selector = model_selector
        self._plugin_runtime_mode = plugin_runtime_mode
        self._plugin_resolver = plugin_resolver
        self._connectivity_resolver = connectivity_resolver

    async def freeze_retained_revision_graph(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedAgentRevisionGraph,
    ) -> None:
        """Recheck all retained graph evidence inside the final transaction."""

        for index, invocation in enumerate(prepared.invocations):
            await self.freeze_in_transaction(
                session,
                prepared=invocation,
                _root_state_policy=(prepared.root_state_policy if index == 0 else RootAgentStatePolicy.invocable),
            )

    async def freeze_in_transaction(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedAgentInvocation,
        _root_state_policy: RootAgentStatePolicy = RootAgentStatePolicy.invocable,
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
                policy=_root_state_policy,
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
            if (
                revision_record.content_digest != prepared.revision_content_digest
                or revision_record.plugin_runtime_mode != self._plugin_runtime_mode.value
            ):
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
            try:
                plugins = await self._plugin_resolver.freeze_in_transaction(
                    session,
                    actor=prepared.actor,
                    workspace_id=prepared.workspace_id,
                    prepared=prepared.plugins,
                )
            except PluginSelectionError as error:
                raise agent_revision_not_executable(error.reason) from error
            subagents = await freeze_subagents(session, prepared)
            connectivity = await freeze_invocation_connectivity(
                self._connectivity_resolver,
                session,
                prepared.connectivity,
            )
        except AuthorizationError as error:
            raise map_authorization_error(error) from error

        resolved_subagents = tuple(item.edge for item in subagents)
        try:
            if runtime_selection_unchanged(prepared, plugins, resolved_subagents):
                runtime_lock = await self._plugin_resolver.runtime_locks.require(
                    session,
                    prepared.revision.runtime_lock_digest,
                    mode=prepared.revision.plugin_runtime_mode.value,
                )
            else:
                runtime_lock = await self._plugin_resolver.freeze_runtime_lock(
                    session,
                    prepared=prepared.plugins,
                    child_lock_digests=tuple(item.child_runtime_lock_digest for item in subagents),
                    use_active_catalog=True,
                )
        except PluginRuntimeLockError as error:
            raise agent_revision_not_executable(error.reason) from error
        config_payload = {
            "schema_version": "1",
            "resolved_model": EffectiveAgentModel(
                execution=execution,
                settings=effective_settings(
                    execution.model_api, prepared.model.resource.settings, prepared.merged.config.model.settings
                ),
                characteristics=prepared.merged.config.model.characteristics,
            ),
            "resolved_plugin_versions": plugins,
            "runtime_lock_digest": runtime_lock.digest,
            "skills": skills,
            "connector_tools": prepared.merged.config.connector_tools,
            "mcp_tools": prepared.merged.config.mcp_tools,
            "resolved_subagents": resolved_subagents,
            "instructions": prepared.merged.config.instructions,
            "input_adapter": prepared.merged.config.input_adapter,
            "client_tools": prepared.merged.config.client_tools,
            "output_spec": prepared.merged.config.output_spec,
            "retries": prepared.merged.config.retries,
            "secret_requirements": prepared.merged.config.secret_requirements,
            "asset_publication": prepared.merged.config.asset_publication,
            "protocol": prepared.merged.config.protocol,
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
            content_digest=canonical_digest(digest_payload),
        )
        return FrozenAgentInvocation(
            agent_id=prepared.agent_id,
            agent_revision_id=prepared.agent_revision_id,
            selector_kind=prepared.selector_kind,
            effective_config=effective,
            sensitive_values=prepared.merged.sensitive_values,
            sensitive_values_digest=canonical_digest(prepared.merged.sensitive_values),
            connector_connection_selections=(
                connectivity.connector_connection_selections if connectivity is not None else ()
            ),
            mcp_connection_selections=(connectivity.mcp_connection_selections if connectivity is not None else ()),
        )
