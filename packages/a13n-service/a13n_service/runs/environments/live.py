"""Apply durable Run additions before projecting the next root model request."""

from contextlib import AsyncExitStack

from a13n_harness import AgentContext
from a13n_harness.environment.providers import EnvironmentRuntime
from a13n_harness.model_context import (
    AbstractModelContextCapability,
    ModelContextNext,
    ModelContextProjection,
    ModelContextProjectionRequest,
)
from a13n_logging import get_logger
from pydantic_ai import RunContext
from pydantic_ai.capabilities import CapabilityOrdering

from a13n_service.infra.db import transaction
from a13n_service.runs.attempts import Lease, lock_lease
from a13n_service.runs.environments.clients.recovery import Recovery
from a13n_service.runs.environments.execution import Source, open_mounts
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EnvironmentMount, RunOptions
from a13n_service.runs.tool_executions.store import MOUNTS
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal

logger = get_logger(__name__)


class LiveMounts(AbstractModelContextCapability):
    id = "a13n.service.environment-mounts"

    def __init__(
        self,
        runtime: Runtime,
        lease: Lease,
        principal: Principal,
        authority: ExecutionAuthority,
        environment: EnvironmentRuntime,
        initial: tuple[EnvironmentMount, ...],
        stack: AsyncExitStack,
        recovery: Recovery,
    ) -> None:
        self.runtime, self.lease = runtime, lease
        self.principal, self.execution_authority = principal, authority
        self.environment, self.mounts, self.stack, self.recovery = environment, initial, stack, recovery

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost", wraps=(Recovery,))

    async def wrap_model_context(
        self,
        ctx: RunContext[AgentContext],
        request: ModelContextProjectionRequest,
        handler: ModelContextNext,
    ) -> ModelContextProjection:
        async with transaction(self.runtime.storage) as session:
            run, _, _ = await lock_lease(session, self.lease)
            accepted = tuple(EnvironmentMount.model_validate(m) for m in run.environment_mounts)
            configuration = RunOptions.model_validate(run.options).configuration
        known = {mount.name for mount in self.mounts}
        additions = tuple(mount for mount in accepted if mount.name not in known)
        if additions:
            sources = await self.stack.enter_async_context(
                open_mounts(
                    self.runtime,
                    self.lease,
                    self.principal,
                    self.execution_authority,
                    additions,
                    configuration=configuration,
                )
            )
            for name, mount in sources.items():
                await self.environment.mount(name, mount)
                source = mount.source
                if isinstance(source, Source) and source.client is not None:
                    assert source.session_recovery is not None
                    self.recovery.mounts[name] = mount, source
                    self.recovery.sessions[name] = source.session_recovery
            self.mounts = accepted
            logger.info(
                "Run environment mounts applied at model boundary",
                extra={
                    "run_id": self.lease.run_id,
                    "mount_names": [mount.name for mount in additions],
                },
            )
        # A new immutable value is inherited by future tools. Already-started tasks keep their own scope.
        MOUNTS.set(self.mounts)
        return await handler(request)
