"""Prepare the accepted root and inline child resources in one Attempt-owned scope."""

from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass

from a13n_harness import AgentContext
from a13n_harness.capabilities import SkillsCapability
from pydantic_ai.capabilities import AbstractCapability
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.agents.execution_graph import inline_child_executions
from a13n_service.agents.reconstruction import AgentDefinitionReconstructionContext
from a13n_service.agents.toolsets import enabled_tool
from a13n_service.assets.runtime import AssetCapability, AssetRuntime, PublicationSelection
from a13n_service.connectivity.execution import ExternalToolRuntime
from a13n_service.connectivity.selection_resolution import FrozenRunConnectivity
from a13n_service.iam.authorization import WorkspaceAction, authorize_persisted_agent_principal_actions
from a13n_service.models.domain import ModelExecutionSnapshot
from a13n_service.skills.attempts import CurrentSkillAttempt
from a13n_service.skills.runtime import PreparedSkillRuntime, SkillRuntimePreparer
from a13n_service.storage import short_session

from .attempts import AttemptContext
from .domain import Run


@dataclass(frozen=True, slots=True)
class PreparedAgentResources:
    capabilities: dict[str | None, tuple[AbstractCapability[AgentContext], ...]]
    models: tuple[ModelExecutionSnapshot, ...]

    def for_definition(
        self, context: AgentDefinitionReconstructionContext
    ) -> tuple[AbstractCapability[AgentContext], ...]:
        return self.capabilities.get(context.agent_revision_id, ())


async def validate_agent_resources(
    *,
    sessions: async_sessionmaker[AsyncSession],
    run: Run,
    workspace_id: str,
    config: EffectiveAgentConfig,
    current_context: Callable[[], AttemptContext],
    skills: SkillRuntimePreparer,
    external_tools: ExternalToolRuntime,
    working_directory: str = "/",
) -> dict[str | None, PreparedSkillRuntime]:
    """Validate retained dependencies without opening execution resources."""
    children = inline_child_executions(config)
    async with short_session(sessions) as session:
        for agent_id in {run.agent_id, *(edge.child_agent_id for edge, _ in children.values())}:
            if run.configuration_context is not None and agent_id == run.agent_id:
                from a13n_service.agent_configuration.authorization import authorize_execution

                await authorize_execution(
                    session,
                    principal=run.authority_principal,
                    organization_id=run.organization_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    context=run.configuration_context,
                    snapshot=current_context().authorization.snapshot,
                )
                continue
            await authorize_persisted_agent_principal_actions(
                session,
                principal=run.authority_principal,
                organization_id=run.organization_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                actions=frozenset({WorkspaceAction.agent_invoke}),
                snapshot=current_context().authorization.snapshot,
            )
    if run.configuration_context is None:
        await external_tools.validate(current_context)
    configurations = {run.agent_revision_id: config}
    for revision_id, (edge, child) in children.items():
        await external_tools.validate(
            current_context,
            child_agent_id=edge.child_agent_id,
            selections=FrozenRunConnectivity(child.connection_selections),
        )
        configurations[revision_id] = child.effective_config
    prepared: dict[str | None, PreparedSkillRuntime] = {}
    for revision_id, selected in configurations.items():
        prepared[revision_id] = await skills.prepare(
            organization_id=run.organization_id,
            workspace_id=workspace_id,
            locks=selected.skills,
            fence=CurrentSkillAttempt(sessions, current_context),
            working_directory=working_directory,
        )
    return prepared


async def prepare_agent_resources(
    *,
    run: Run,
    workspace_id: str,
    asset_publication: AssetRuntime,
    config: EffectiveAgentConfig,
    current_context: Callable[[], AttemptContext],
    skills: dict[str | None, PreparedSkillRuntime],
    external_tools: ExternalToolRuntime,
    stack: AsyncExitStack,
) -> PreparedAgentResources:
    """Open fresh root and inline-child clients in the owning Attempt resource scope."""
    children = inline_child_executions(config)
    capabilities: dict[str | None, tuple[AbstractCapability[AgentContext], ...]] = {}
    capabilities[run.agent_revision_id] = (
        await stack.enter_async_context(external_tools.capabilities(current_context))
        if run.configuration_context is None
        else ()
    )
    configurations = {run.agent_revision_id: config}
    for revision_id, (edge, child) in children.items():
        capabilities[revision_id] = await stack.enter_async_context(
            external_tools.child_capabilities(
                current_context,
                agent_id=edge.child_agent_id,
                selections=FrozenRunConnectivity(child.connection_selections),
            )
        )
        configurations[revision_id] = child.effective_config
    for revision_id, selected in configurations.items():
        if enabled_tool(selected.toolsets, "assets", "publish") is not None:
            assert revision_id is not None
            agent_id = run.agent_id if revision_id == run.agent_revision_id else children[revision_id][0].child_agent_id
            capabilities[revision_id] = (
                *capabilities[revision_id],
                AssetCapability(
                    asset_publication,
                    current_context,
                    PublicationSelection(workspace_id, agent_id, revision_id, run.effective_agent_config_digest),
                ),
            )
        runtime = skills[revision_id]
        if runtime.manager is not None:
            capabilities[revision_id] = (*capabilities[revision_id], SkillsCapability(runtime.manager))
    return PreparedAgentResources(
        capabilities,
        tuple(
            model.execution
            for selected in configurations.values()
            for model in (
                selected.resolved_model,
                selected.resolved_reviewer_model,
                *selected.media_understanding.values(),
            )
            if model is not None
        ),
    )
