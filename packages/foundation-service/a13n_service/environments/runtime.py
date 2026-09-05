"""Ready or lazy Host-owned operation objects for an accepted Run."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import TYPE_CHECKING

from a13n_environment_provider import (
    Environment,
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentOperationFamily,
    EnvironmentOperations,
    EnvironmentState,
)
from a13n_logging import get_logger
from anyio import fail_after

from a13n_service.interactions.models import RunRecord
from a13n_service.storage import short_session

from .configuration import load_configuration
from .domain import TemplateConfiguration
from .lifecycle import EnvironmentLifecycle, EnvironmentOperationBusy
from .models import EnvironmentProviderRecord, EnvironmentRecord

if TYPE_CHECKING:
    from a13n_service.interactions.attempts import AttemptContext

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
    ) -> None:
        super().__init__(None)
        self._coordinator = lifecycle
        self._attempt = attempt
        self._environment_id = environment_id
        self._provider_key = provider_key
        self._configured_descriptor = descriptor
        self._delegate: Environment | None = None
        self._generation = 0
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

    async def _prepare(
        self, *, thread_id: str, run_id: str, agent_instance_id: str, mount_id: str, host_refs: Mapping[str, str]
    ) -> None:
        if self._delegate is not None:
            self._cache_state(self._delegate.dump_state())
            delegate, self._delegate = self._delegate, None
            try:
                with fail_after(10, shield=True):
                    await delegate.close()
            except Exception:
                logger.exception("Discarded Environment connection cleanup failed")
        async with asyncio.timeout(self._coordinator.timeout_seconds + 10):
            while True:
                try:
                    operation = await self._coordinator.acquire(self.environment_id, "prepare", attempt=self._attempt)
                    break
                except EnvironmentOperationBusy:
                    await asyncio.sleep(0.1)
        result = await self._coordinator.execute(operation)
        delegate = result.environment
        self._cache_state(delegate.dump_state())
        try:
            await delegate.enter(
                thread_id=thread_id,
                run_id=run_id,
                agent_instance_id=agent_instance_id,
                mount_id=mount_id,
                host_refs=host_refs,
            )
        except BaseException as error:
            try:
                with fail_after(10, shield=True):
                    await delegate.close()
            except BaseException as cleanup_error:
                error.add_note(f"Environment cleanup also failed: {cleanup_error!r}")
            raise
        self._delegate, self._generation = delegate, result.generation

    def _bind_mount(self, mount_id: str) -> None:
        if self._delegate is not None:
            self._delegate.bind_mount(mount_id)

    async def _ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        if self._delegate is None:
            raise RuntimeError("Environment was not prepared")
        await self._coordinator.validate_use(self._attempt, self.environment_id)
        await self._delegate.ensure_ready(operations)

    def dump_state(self) -> EnvironmentState | None:
        return self._delegate.dump_state() if self._delegate else super().dump_state()

    async def _close(self) -> None:
        if self._delegate is not None:
            await self._delegate.close()

    async def _destroy(self) -> None:
        raise RuntimeError("Run objects do not own target deletion authority")


async def prepare_run_environment(lifecycle: EnvironmentLifecycle, attempt: AttemptContext) -> RunEnvironment | None:
    async with short_session(lifecycle.sessions) as session:
        run = await session.get(RunRecord, attempt.run_id)
        if run is None or run.tenant_id != attempt.tenant_id:
            raise ValueError("Run is unavailable")
        if run.environment_id is None:
            return None
        row = await session.get(EnvironmentRecord, run.environment_id)
        if row is None:
            raise ValueError("Environment is unavailable")
        provider = await session.get(EnvironmentProviderRecord, row.provider_id)
        if provider is None or not provider.enabled:
            raise ValueError("Environment Provider is unavailable")
        configuration = await load_configuration(session, row)
        implementation = lifecycle.catalog.require(provider.type)
        validated = implementation.validate_configuration(
            schema_version=configuration.configuration_schema_version, value=configuration.configuration
        )
        descriptor = implementation.describe_configuration(validated)
        if run.environment_access is None:
            raise ValueError("Run Environment access is missing")
        environment = RunEnvironment(lifecycle, attempt, row.id, provider.type, descriptor, run.environment_access)
    if not isinstance(configuration, TemplateConfiguration) or configuration.preparation == "on_run":
        await environment.prepare()
    return environment
