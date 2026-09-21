"""Open Attempt-owned Environments and bind their shared live mount runtime."""

from contextlib import AsyncExitStack
from dataclasses import dataclass

from a13n_harness import EnvironmentMount
from a13n_harness.environment import EnvironmentPermissionSet
from a13n_harness.providers.environment.management import Environment
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.environments.mount_domain import AcceptedRunMount
from a13n_service.environments.mount_observations import RunMountObservations
from a13n_service.environments.mount_runtime import RunMountRuntime
from a13n_service.environments.runtime import prepare_run_environment
from a13n_service.environments.websocket.worker_connections import WorkerClientConnections

from .environment_observation import EnvironmentHookProjector, observe_environment_entry
from .harness_runtime import MountedHarnessEnvironments
from .run_control import RunAttemptControl


@dataclass(frozen=True, slots=True)
class AttemptEnvironments:
    sessions: async_sessionmaker[AsyncSession]
    lifecycle: EnvironmentLifecycle
    control: RunAttemptControl
    projector: EnvironmentHookProjector
    client_connections: WorkerClientConnections | None = None

    def observe(
        self, environment: Environment, permission_ceiling: EnvironmentPermissionSet | None = None
    ) -> EnvironmentMount:
        mount = (
            EnvironmentMount(environment)
            if permission_ceiling is None
            else EnvironmentMount(environment, permission_ceiling=permission_ceiling)
        )
        return observe_environment_entry(mount, self.projector)

    async def open_managed(self, stack: AsyncExitStack) -> MountedHarnessEnvironments:
        environment = await prepare_run_environment(
            self.lifecycle, self.control.current_context, client_connections=self.client_connections
        )
        if environment is not None:
            stack.push_async_callback(environment.close)
        mounted = MountedHarnessEnvironments(
            entries=({"workspace": self.observe(environment)} if environment is not None else {})
        )

        async def prepare_mount(mount: AcceptedRunMount) -> Environment:
            candidate = await prepare_run_environment(
                self.lifecycle,
                self.control.current_context,
                client_connections=self.client_connections,
                mount=mount,
            )
            if candidate is None:
                raise RuntimeError("An accepted additional mount has no Environment")
            return candidate

        await self.control.bind_environment_mounts(
            RunMountRuntime(
                runtime=mounted.runtime,
                has_primary=environment is not None,
                observations=RunMountObservations(self.sessions, clock=self.lifecycle.clock),
                current_attempt=lambda: self.control.current_context,
                prepare=prepare_mount,
                observe=self.observe,
            )
        )
        return mounted
