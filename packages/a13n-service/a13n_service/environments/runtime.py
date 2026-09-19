"""Ready or lazy Host-owned operation objects for an accepted Run."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from a13n_harness.observation import record_span_metadata
from a13n_harness.providers.environment.management import Environment
from a13n_harness.providers.environment.models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentOperationFamily,
    EnvironmentState,
)
from a13n_harness.providers.environment.operations import EnvironmentOperations
from a13n_harness.providers.environment.remote_envd.connections import WEBSOCKET_PROVIDER_KEY
from a13n_logging import get_logger
from anyio import fail_after

from a13n_service.iam.authorization import WorkspaceAction, authorize_persisted_agent_principal_actions
from a13n_service.interactions.models import RunRecord
from a13n_service.observability import observe_phase, observe_phase_result
from a13n_service.storage import short_session

from .configuration import load_configuration
from .domain import TemplateConfiguration
from .lifecycle import EnvironmentLifecycle, EnvironmentOperationBusy
from .local_directory import instance_configuration
from .models import EnvironmentProviderRecord, EnvironmentRecord
from .mount_domain import AcceptedRunMount
from .mount_models import RunEnvironmentMountRecord
from .run_use import load_run_environment_binding
from .websocket.environment import ClientRunEnvironment
from .websocket.worker_resources import ClientUseResources

if TYPE_CHECKING:
    from a13n_service.interactions.attempts import AttemptContext

    from .websocket.worker_connections import WorkerClientConnections

logger = get_logger(__name__)


class RunEnvironment(Environment):
    """Hold one logical selection while the lifecycle owner supplies current target state."""

    recover_on_unavailable = True

    def __init__(
        self,
        lifecycle: EnvironmentLifecycle,
        attempt: AttemptContext,
        environment_id: str,
        provider_key: str,
        descriptor: EnvironmentDescriptor,
        access: str,
        *,
        mount_name: str = "workspace",
    ) -> None:
        super().__init__(None)
        self._coordinator = lifecycle
        self._attempt = attempt
        self._environment_id = environment_id
        self._mount_name = mount_name
        self._provider_key = provider_key
        self._configured_descriptor = descriptor
        self._delegate: Environment | None = None
        self._generation = 0
        self._credential_generation: int | None = None
        self.access = access

    @property
    def backing_generation(self) -> int:
        return self._generation

    @property
    def provider_key(self) -> str:
        return self._provider_key

    @property
    def environment_id(self) -> str:
        return self._environment_id

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        if self._delegate is None:
            return self._configured_descriptor
        return self._delegate.descriptor.model_copy(
            update={
                "backing_identity": f"{self.environment_id}:{self._generation}",
            }
        )

    @property
    def availability(self) -> EnvironmentAvailability:
        return self._delegate.availability if self._delegate else EnvironmentAvailability(status="preparing")

    @property
    def operations(self) -> EnvironmentOperations:
        return self._delegate.operations if self._delegate else EnvironmentOperations()

    async def _prepare(self, *, mount_id: str) -> None:
        # This boundary runs for eager preparation, first use, and target recovery.
        with observe_phase("a13n.service.environment.prepare") as span:
            previous_generation = self.backing_generation
            if span is not None:
                record_span_metadata(
                    span, {"environment.id": self.environment_id, "environment.generation_before": previous_generation}
                )
            await self._prepare_target(
                mount_id=mount_id,
            )
            if span is not None:
                record_span_metadata(span, {"environment.generation": self.backing_generation})
            observe_phase_result(
                span,
                generation_before=previous_generation,
                generation_after=self.backing_generation,
                generation_changed=previous_generation != self.backing_generation,
            )

    async def _prepare_target(self, *, mount_id: str) -> None:
        delay = 0.1
        async with asyncio.timeout(self._coordinator.lease_duration.total_seconds()):
            while True:
                try:
                    operation = await self._coordinator.acquire_preparation(
                        self.environment_id, attempt=self._attempt, mount_name=self._mount_name
                    )
                    break
                except EnvironmentOperationBusy:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 1)
        delegate, self._delegate = self._delegate, None
        if delegate is not None:
            self._cache_state(delegate.dump_state())
            reusable = (
                delegate.recover_on_unavailable
                and delegate.dump_state() == operation.state
                and self._credential_generation == operation.credential.generation
            )
            if not reusable:
                try:
                    with fail_after(10, shield=True):
                        await delegate.close()
                except Exception:
                    logger.exception("Discarded Environment connection cleanup failed")
                delegate = None
        result = await self._coordinator.execute(operation, recovering=delegate)
        delegate = result.environment
        self._cache_state(delegate.dump_state())
        try:
            if not delegate.is_entered:
                await delegate.enter(mount_id=mount_id)
        except BaseException as error:
            try:
                with fail_after(10, shield=True):
                    await delegate.close()
            except BaseException as cleanup_error:
                error.add_note(f"Environment cleanup also failed: {cleanup_error!r}")
            raise
        self._delegate, self._generation = delegate, result.generation
        self._credential_generation = operation.credential.generation

    def _bind_mount(self, mount_id: str) -> None:
        if self._delegate is not None:
            self._delegate.bind_mount(mount_id)

    async def _ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        if self._delegate is None:
            raise RuntimeError("Environment was not prepared")
        self._validate_use()
        await self._delegate.check_ready(operations)
        self._validate_use()

    def _validate_use(self) -> None:
        self._attempt.lease.require_current(self._coordinator.clock())
        self._attempt.authorization.require_environment(self.environment_id)

    def dump_state(self) -> EnvironmentState | None:
        return self._delegate.dump_state() if self._delegate else super().dump_state()

    async def _close(self) -> None:
        if self._delegate is not None:
            await self._delegate.close()

    async def _destroy(self) -> None:
        raise RuntimeError("Run objects do not own target deletion authority")


@dataclass(frozen=True, slots=True)
class _RunEnvironmentSelection:
    mount_name: str
    environment_id: str
    provider_key: str
    descriptor: EnvironmentDescriptor
    access: str
    prepare_on_run: bool


async def validate_run_environment(
    lifecycle: EnvironmentLifecycle, attempt: AttemptContext, *, mount: AcceptedRunMount | None = None
) -> _RunEnvironmentSelection | None:
    """Validate the fixed logical selection without acquiring use or calling a Provider."""

    async with short_session(lifecycle.sessions) as session:
        run = await session.get(RunRecord, attempt.run_id)
        if run is None or run.organization_id != attempt.organization_id:
            raise ValueError("Run is unavailable")
        binding = await load_run_environment_binding(
            session, run, name=mount.name if mount is not None else "workspace"
        )
        if binding is None:
            if mount is not None:
                raise ValueError("The accepted Run mount is unavailable")
            return None
        if mount is not None and (
            not isinstance(binding.record, RunEnvironmentMountRecord)
            or AcceptedRunMount.from_record(binding.record) != mount
        ):
            raise ValueError("The accepted Run mount changed")
        row = await session.get(EnvironmentRecord, binding.environment_id)
        if row is None or (row.organization_id, row.workspace_id) != (run.organization_id, binding.workspace_id):
            raise ValueError("Environment is unavailable")
        await authorize_persisted_agent_principal_actions(
            session,
            principal=run.to_resource().authority_principal,
            organization_id=row.organization_id,
            workspace_id=row.workspace_id,
            agent_id=run.agent_id,
            actions=frozenset({WorkspaceAction.environment_use, WorkspaceAction.agent_invoke}),
            snapshot=attempt.authorization.snapshot,
        )
        provider = await session.get(EnvironmentProviderRecord, row.provider_id)
        if (
            provider is None
            or not provider.enabled
            or provider.organization_id != row.organization_id
            or provider.workspace_id not in {None, row.workspace_id}
        ):
            raise ValueError("Environment Provider is unavailable")
        configuration = await load_configuration(session, row)
        implementation = lifecycle.catalog.require(provider.type)
        validated = implementation.validate_environment(
            schema_version=configuration.configuration_schema_version,
            value=instance_configuration(provider.type, row.id, configuration),
        )
        descriptor = implementation.describe_environment(validated)
        return _RunEnvironmentSelection(
            binding.name,
            row.id,
            provider.type,
            descriptor,
            binding.access,
            not isinstance(configuration, TemplateConfiguration) or configuration.preparation == "on_run",
        )


async def prepare_run_environment(
    lifecycle: EnvironmentLifecycle,
    attempt: AttemptContext,
    *,
    client_connections: WorkerClientConnections | None = None,
    mount: AcceptedRunMount | None = None,
) -> RunEnvironment | ClientRunEnvironment | None:
    if mount is not None:
        if mount.run_id != attempt.run_id:
            raise ValueError("Mount does not belong to this Attempt's Run")
        await attempt.authorization.admit_environment(name=mount.name, environment_id=mount.environment_id)
    selection = await validate_run_environment(lifecycle, attempt, mount=mount)
    if selection is None:
        return None
    if selection.provider_key == WEBSOCKET_PROVIDER_KEY:
        if client_connections is None:
            from a13n_harness.providers.environment.models import EnvironmentError

            raise EnvironmentError("Client Environment support is unavailable", code="environment_worker_incompatible")
        environment = ClientRunEnvironment(
            ClientUseResources(lifecycle.sessions, lifecycle.capacity),
            client_connections,
            attempt,
            selection.environment_id,
            selection.descriptor,
            selection.access,
            mount_name=selection.mount_name,
        )
    else:
        environment = RunEnvironment(
            lifecycle,
            attempt,
            selection.environment_id,
            selection.provider_key,
            selection.descriptor,
            selection.access,
            mount_name=selection.mount_name,
        )
    try:
        if mount is not None or selection.prepare_on_run:
            await environment.prepare()
    except BaseException as error:
        try:
            with fail_after(attempt.cleanup_timeout.total_seconds(), shield=True):
                await environment.close()
        except BaseException as cleanup_error:
            error.add_note(f"Environment preparation cleanup also failed: {cleanup_error!r}")
        raise
    return environment
