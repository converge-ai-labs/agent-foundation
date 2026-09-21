"""Protected assistant execution policy, tools, and deployment-owned knowledge."""

from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass

from a13n_harness import AgentContext
from a13n_harness.capabilities import UserInteractionCapability
from a13n_harness.environment import FILE_READ_ACTIONS, EnvironmentPermissionSet
from a13n_harness.errors import RunError
from anyio import to_thread
from pydantic_ai.capabilities import AbstractCapability
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.gateway.queries import NativeInteractionQueries
from a13n_service.iam.authorization import PrincipalPermissions, WorkspaceAction
from a13n_service.interactions.attempt_environments import AttemptEnvironments
from a13n_service.interactions.attempts import AttemptContext
from a13n_service.interactions.domain import Run
from a13n_service.interactions.harness_runtime import MountedHarnessEnvironments
from a13n_service.run_stream import RunDisplayStore

from .authorization import authorize_execution
from .drafts import ConfigurationDrafts
from .knowledge import KnowledgeFiles, knowledge_capability
from .resources import ConfigurationResources
from .runtime import ConfigurationCapability, validate_configuration_definition


@dataclass(frozen=True, slots=True)
class ConfigurationExecution:
    sessions: async_sessionmaker[AsyncSession]
    run: Run
    workspace_id: str
    current_context: Callable[[], AttemptContext]
    drafts: ConfigurationDrafts | None
    display: RunDisplayStore

    @property
    def root_policy(self) -> "ConfigurationExecution":
        return self

    @property
    def required_actions(self) -> frozenset[WorkspaceAction]:
        # This protected root uses draft ownership, not an agent.invoke grant.
        # Every IAM refresh checks that ownership; ordinary children still require invoke.
        return frozenset()

    async def refresh(self, session: AsyncSession, snapshot: PrincipalPermissions) -> None:
        await self.authorize_root(session, snapshot)

    async def authorize_root(self, session: AsyncSession, snapshot: PrincipalPermissions) -> None:
        binding = self.run.configuration_context
        if binding is None:
            raise ValueError("Configuration execution requires a protected Run binding")
        await authorize_execution(
            session,
            principal=self.run.authority_principal,
            organization_id=self.run.organization_id,
            workspace_id=self.workspace_id,
            agent_id=self.run.agent_id,
            context=binding,
            snapshot=snapshot,
        )

    async def validate(self, config: EffectiveAgentConfig) -> None:
        if self.drafts is None:
            raise RunError("Configuration tools are unavailable.", code="configuration_worker_incompatible")
        validate_configuration_definition(run=self.run, config=config)
        await to_thread.run_sync(KnowledgeFiles().validate)

    async def validate_tools(self) -> None:
        # The protected definition admits only its fixed host-provided tools.
        pass

    async def open_capabilities(self, stack: AsyncExitStack) -> tuple[AbstractCapability[AgentContext], ...]:
        if self.drafts is None:
            raise RunError("Configuration tools are unavailable.", code="configuration_worker_incompatible")
        return (
            ConfigurationCapability(
                self.sessions,
                self.drafts,
                ConfigurationResources(self.sessions),
                NativeInteractionQueries(self.sessions, self.display),
                run=self.run,
                workspace_id=self.workspace_id,
                current_context=self.current_context,
            ),
            knowledge_capability(),
            UserInteractionCapability(),
        )

    async def open_environment(
        self, resources: AttemptEnvironments, stack: AsyncExitStack
    ) -> MountedHarnessEnvironments:
        environment = await to_thread.run_sync(KnowledgeFiles().environment)
        return MountedHarnessEnvironments(
            entries={
                "builtin-skills": resources.observe(environment, EnvironmentPermissionSet(operations=FILE_READ_ACTIONS))
            },
            default_environment="builtin-skills",
        )
