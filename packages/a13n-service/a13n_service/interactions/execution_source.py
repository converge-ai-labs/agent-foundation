"""Trusted execution-source integration, selected once by Worker composition."""

from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Protocol

from a13n_harness import AgentContext
from pydantic_ai.capabilities import AbstractCapability
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.connectivity.execution import ExternalToolRuntime
from a13n_service.iam.attempts import RootExecutionPolicy
from a13n_service.iam.authorization import (
    PrincipalPermissions,
    WorkspaceAction,
    authorize_persisted_agent_principal_actions,
)

from .attempt_environments import AttemptEnvironments
from .attempts import AttemptContext
from .domain import Run
from .harness_runtime import MountedHarnessEnvironments


class ExecutionSource(Protocol):
    """Supply root policy and resources without owning Attempt scheduling or cleanup."""

    @property
    def root_policy(self) -> RootExecutionPolicy | None: ...

    async def authorize_root(self, session: AsyncSession, snapshot: PrincipalPermissions) -> None: ...

    async def validate(self, config: EffectiveAgentConfig) -> None: ...

    async def validate_tools(self) -> None: ...

    async def open_capabilities(self, stack: AsyncExitStack) -> tuple[AbstractCapability[AgentContext], ...]: ...

    async def open_environment(
        self, resources: AttemptEnvironments, stack: AsyncExitStack
    ) -> MountedHarnessEnvironments: ...


@dataclass(frozen=True, slots=True)
class PublishedAgentExecution:
    run: Run
    workspace_id: str
    current_context: Callable[[], AttemptContext]
    external_tools: ExternalToolRuntime

    @property
    def root_policy(self) -> RootExecutionPolicy | None:
        return None

    async def authorize_root(self, session: AsyncSession, snapshot: PrincipalPermissions) -> None:
        await authorize_persisted_agent_principal_actions(
            session,
            principal=self.run.authority_principal,
            organization_id=self.run.organization_id,
            workspace_id=self.workspace_id,
            agent_id=self.run.agent_id,
            actions=frozenset({WorkspaceAction.agent_invoke}),
            snapshot=snapshot,
        )

    async def validate(self, config: EffectiveAgentConfig) -> None:
        # Published definitions are checked by the shared graph reconstruction.
        pass

    async def validate_tools(self) -> None:
        await self.external_tools.validate(self.current_context)

    async def open_capabilities(self, stack: AsyncExitStack) -> tuple[AbstractCapability[AgentContext], ...]:
        return await stack.enter_async_context(self.external_tools.capabilities(self.current_context))

    async def open_environment(
        self, resources: AttemptEnvironments, stack: AsyncExitStack
    ) -> MountedHarnessEnvironments:
        return await resources.open_managed(stack)
