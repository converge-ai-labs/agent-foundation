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
from a13n_service.connectivity.execution import ExternalToolRuntime
from a13n_service.connectivity.selection_resolution import FrozenRunConnectivity
from a13n_service.iam.authorization import WorkspaceAction, authorize_persisted_agent_principal_actions
from a13n_service.models.domain import ModelExecutionSnapshot
from a13n_service.skills.runtime import SkillRuntimePreparer
from a13n_service.storage import short_session

from .attempts import AttemptContext
from .domain import Run


@dataclass(frozen=True, slots=True)
class PreparedAgentResources:
    capabilities: dict[str, tuple[AbstractCapability[AgentContext], ...]]
    models: tuple[ModelExecutionSnapshot, ...]

    def for_definition(
        self, context: AgentDefinitionReconstructionContext
    ) -> tuple[AbstractCapability[AgentContext], ...]:
        return self.capabilities.get(context.agent_revision_id, ())


async def prepare_agent_resources(
    *,
    sessions: async_sessionmaker[AsyncSession],
    run: Run,
    workspace_id: str,
    config: EffectiveAgentConfig,
    current_context: Callable[[], AttemptContext],
    skills: SkillRuntimePreparer,
    external_tools: ExternalToolRuntime,
    stack: AsyncExitStack,
) -> PreparedAgentResources:
    children = inline_child_executions(config)
    async with short_session(sessions) as session:
        for agent_id in {run.agent_id, *(edge.child_agent_id for edge, _ in children.values())}:
            await authorize_persisted_agent_principal_actions(
                session,
                principal=run.authority_principal,
                organization_id=run.organization_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                actions=frozenset({WorkspaceAction.agent_invoke}),
            )
    capabilities: dict[str, tuple[AbstractCapability[AgentContext], ...]] = {}
    root_tools = await stack.enter_async_context(external_tools.capabilities(current_context))
    capabilities[run.agent_revision_id] = root_tools
    configurations = {run.agent_revision_id: config}
    for revision_id, (edge, child) in children.items():
        capabilities[revision_id] = await stack.enter_async_context(
            external_tools.child_capabilities(
                current_context,
                agent_id=edge.child_agent_id,
                selections=FrozenRunConnectivity(
                    child.connector_connection_selections, child.mcp_connection_selections
                ),
            )
        )
        configurations[revision_id] = child.effective_config
    for revision_id, selected in configurations.items():
        runtime = await skills.prepare(
            organization_id=run.organization_id,
            workspace_id=workspace_id,
            locks=selected.skills,
        )
        if runtime.manager is not None:
            capabilities[revision_id] = (*capabilities[revision_id], SkillsCapability(runtime.manager))
    return PreparedAgentResources(
        capabilities,
        tuple(selected.resolved_model.execution for selected in configurations.values()),
    )
