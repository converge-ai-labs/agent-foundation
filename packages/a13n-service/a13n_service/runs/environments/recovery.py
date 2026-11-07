"""Reconnect only the frozen target of an admitted Shell, without lifecycle startup."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from a13n_harness import EnvironmentMount as HarnessMount
from a13n_harness import RunConfiguration
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.environment.execution import EnvironmentConnector
from a13n_harness.environment.models import EnvironmentAction, EnvironmentDescriptor, EnvironmentState
from a13n_harness.environment.process_recovery import ShellExecutionState
from a13n_harness.environment.providers import EnvironmentRuntime
from a13n_harness.tool_execution import ToolExecutionLost, execution_cleanup_active

from a13n_service.infra.db import transaction
from a13n_service.resources.providers.service import read_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.attempts import AuthorityRevoked, Lease, LeaseLost
from a13n_service.runs.environments import connection, external, outbound
from a13n_service.runs.environments.adapters import Target, execution_connector, managed_target, provider_identity
from a13n_service.runs.environments.clients.worker import Target as ClientTarget
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EnvironmentMount, RunOptions
from a13n_service.runs.tool_executions.authority import lock_execution, prove


@dataclass(frozen=True, slots=True)
class _Source:
    connector: EnvironmentConnector

    @property
    def provider_key(self) -> str:
        return self.connector.provider_key

    @property
    def environment_id(self) -> str:
        return self.connector.environment_id

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self.connector.descriptor

    @property
    def state(self) -> EnvironmentState | None:
        return self.connector.state

    async def ensure_ready(self) -> EnvironmentConnector:
        return self.connector


async def prepare(runtime: Runtime, lease: Lease, execution_id: str, mount_name: str) -> EnvironmentRuntime:
    """Existing-work credentials also permit reconnecting solely to finish cancellation.

    No model, file tool, command admission or instance lifecycle runs in this scope.
    The native recover operation verifies Device/Session/generation/start identity.
    """
    async with transaction(runtime.storage) as session:
        run, execution, _ = await lock_execution(session, lease, execution_id, cleanup=True)
        mounts = [EnvironmentMount.model_validate(value) for value in execution.environment_mounts]
        mount = next((item for item in mounts if item.name == mount_name), None)
        if mount is None or execution.shell is None:
            raise ToolExecutionLost("The original Shell mount is missing; its command cannot be replayed.")
        shell = ShellExecutionState.model_validate(execution.shell)
        if shell.mount_name != mount.name:
            raise ToolExecutionLost("The original Shell mount selection changed.")
        configuration = RunOptions.model_validate(run.options).configuration or RunConfiguration()
        environment = await session.get(EnvironmentRow, mount.environment_id)
        if environment is None or environment.status != "ready":
            raise ToolExecutionLost("The original Shell environment is no longer ready.")
        target: Target | ClientTarget
        if environment.kind == "external":
            if environment.device_id is None or mount.working_directory is None:
                raise ToolExecutionLost("The original client Shell binding is missing.")
            target = ClientTarget(
                environment.id,
                environment.device_id,
                mount.working_directory,
                mount.name,
                recovery_execution_id=execution_id,
            )
        elif environment.kind == "external_http":
            target = Target(environment.id, external.account(environment), {}, None)
        else:
            assert environment.provider_id is not None and environment.handle is not None
            provider = await read_provider(session, EnvironmentProviderRow, environment.provider_id)
            if environment.provider_identity != provider_identity(runtime.registry, provider):
                raise ToolExecutionLost("The original Shell provider identity changed.")
            handle = Handle.model_validate(environment.handle)
            plan = connection.resolve(runtime.registry.get("environment", provider.type), handle, mount)
            if plan.mode == "envd_ws":
                binding = outbound.binding_for(runtime.registry, environment, provider, executing=True)
                target = ClientTarget(
                    environment.id,
                    binding.device_id,
                    plan.working_directory,
                    mount.name,
                    handle.state,
                    recovery_execution_id=execution_id,
                )
            else:
                target = managed_target(runtime.keys, environment, provider, handle)
    owner = await runtime.environment_contexts.get(lease, mount)
    recovery = owner.exact(shell.process.session_id)
    connector = (
        target.connector(runtime, lease, recovery)
        if isinstance(target, ClientTarget)
        else await execution_connector(runtime, target, configuration=configuration, session_recovery=recovery)
    )

    @asynccontextmanager
    async def guard(action: EnvironmentAction) -> AsyncIterator[None]:
        try:
            # Recovery must discover the original process before applying durable cancellation.
            if action == EnvironmentAction.PROCESS_INSPECT or (
                execution_cleanup_active()
                and action
                in {
                    EnvironmentAction.PROCESS_KILL,
                    EnvironmentAction.PROCESS_WAIT,
                    EnvironmentAction.PROCESS_RELEASE,
                }
            ):
                async with transaction(runtime.storage) as session:
                    await lock_execution(session, lease, execution_id, cleanup=True)
            else:
                await prove(runtime, lease)
        except LeaseLost:
            runtime.executions.suspend_shell(execution_id)
            raise
        except AuthorityRevoked as exc:
            raise asyncio.CancelledError from exc
        yield

    return create_environment_runtime(
        mounts={
            mount.name: HarnessMount(
                source=_Source(connector),
                operation_guard=guard,
                permission_ceiling=mount.permission_ceiling,
                working_directory=mount.working_directory,
                provider_root=mount.working_directory or connector.descriptor.working_directory,
            )
        },
        default_mount=mount.name,
    )
