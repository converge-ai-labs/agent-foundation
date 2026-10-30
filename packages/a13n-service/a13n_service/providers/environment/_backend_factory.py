"""Single-target management factories with independent client ownership."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import uuid4

from a13n_harness.environment.errors import EnvironmentProviderError, provider_error
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment._runtime import ClosableRuntime, RuntimeFactory, _release
from a13n_harness.providers.environment.remote_envd.managed import ManagedCredentialCache
from pydantic import BaseModel

from ._backend import ManagementBackend
from .desktop_access import Reply, Request
from .errors import EnvironmentManagementCancelled, EnvironmentManagementError
from .management import EnvironmentProvider, EnvironmentStatus
from .metrics import DeviceObservation, MetricsLaunch, ScrapeTarget


class ManagementFactory[E: BaseModel, R](Protocol):
    def __call__(
        self,
        *,
        configuration: E,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: R | None,
        operation_id: str,
    ) -> ManagementBackend: ...


@dataclass(frozen=True, slots=True)
class ManagementBackendFactory[C: BaseModel, K: BaseModel, E: BaseModel, R]:
    key: str
    environment_model: type[E]
    management: ManagementFactory[E, R]
    runtime_factory: RuntimeFactory[C, K, R] | None = None

    async def provider(self, *, configuration: C, credential: K | None, runtime: R | None) -> EnvironmentProvider[E]:
        owned = None
        if runtime is None and self.runtime_factory is not None:
            runtime = await self.runtime_factory(configuration=configuration, credential=credential)
            owned = runtime if isinstance(runtime, ClosableRuntime) else None
        return BackendProvider(self, runtime, owned)


class BackendProvider[C: BaseModel, K: BaseModel, E: BaseModel, R](EnvironmentProvider[E]):
    def __init__(
        self,
        factory: ManagementBackendFactory[C, K, E, R],
        runtime: R | None,
        owned: ClosableRuntime | None,
    ) -> None:
        self._factory = factory
        self._runtime = runtime
        self._owned = owned
        self._close_task: asyncio.Task[None] | None = None

    def _recipe(self, value: object) -> E:
        if self._close_task is not None:
            raise provider_error(self._factory.key, "provider_closed", Category.UNAVAILABLE)
        try:
            return self._factory.environment_model.model_validate(value)
        except ValueError as error:
            raise provider_error(self._factory.key, "provider_spec_invalid", Category.INVALID) from error

    def _target(
        self, environment: object, environment_id: str, state: EnvironmentState | None, operation_id: str
    ) -> ManagementBackend:
        return self._factory.management(
            configuration=self._recipe(environment),
            environment_id=environment_id,
            state=state,
            runtime=self._runtime,
            operation_id=operation_id,
        )

    @asynccontextmanager
    async def _operation(
        self, environment: object, environment_id: str, state: EnvironmentState | None, operation_id: str
    ) -> AsyncGenerator[ManagementBackend]:
        target = self._target(environment, environment_id, state, operation_id)
        primary: BaseException | None = None
        try:
            target.validate_management()
            yield target
        except asyncio.CancelledError as error:
            primary = EnvironmentManagementCancelled(target.state, operation_id)
            raise primary from error
        except Exception as error:
            failure = (
                error
                if isinstance(error, EnvironmentProviderError)
                else provider_error(
                    self._factory.key,
                    "provider_unknown_outcome",
                    Category.UNKNOWN_OUTCOME,
                    certainty=Certainty.UNKNOWN,
                )
            )
            primary = EnvironmentManagementError(failure, target.state, operation_id, environment_id)
            raise primary from error
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                await _release(target, None)
            except BaseException as error:
                if primary is not None:
                    primary.add_note(f"Environment management cleanup also failed: {error!r}")
                else:
                    failure = provider_error(
                        self._factory.key, "provider_cleanup_failed", Category.CLEANUP, certainty=Certainty.KNOWN
                    )
                    raise EnvironmentManagementError(failure, target.state, operation_id, environment_id) from error

    async def create(
        self,
        environment: object,
        *,
        environment_id: str,
        operation_id: str,
        state: EnvironmentState | None = None,
        metrics: MetricsLaunch | None = None,
    ) -> EnvironmentState | None:
        async with self._operation(environment, environment_id, state, operation_id) as target:
            if metrics is not None:
                target.configure_metrics(metrics)
            await target.create()
            return target.state

    async def start(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None
    ) -> EnvironmentState | None:
        async with self._operation(environment, environment_id, state, operation_id) as target:
            await target.start()
            return target.state

    async def inspect(
        self, environment: object, *, environment_id: str, state: EnvironmentState | None
    ) -> EnvironmentStatus:
        async with self._operation(environment, environment_id, state, "op-" + uuid4().hex) as target:
            status = await target.inspect()
            return EnvironmentStatus(status, target.state)

    async def stop(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None
    ) -> EnvironmentState | None:
        async with self._operation(environment, environment_id, state, operation_id) as target:
            await target.stop()
            return target.state

    async def destroy(
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None
    ) -> None:
        async with self._operation(environment, environment_id, state, operation_id) as target:
            await target.destroy()
            target._cache_state(None)

    async def keepalive(
        self,
        environment: object,
        *,
        environment_id: str,
        state: EnvironmentState | None,
        deadline: datetime,
        operation_id: str,
    ) -> datetime | None:
        async with self._operation(environment, environment_id, state, operation_id) as target:
            return await target.keepalive(deadline=deadline, operation_id=operation_id)

    def keepalive_horizon(
        self, environment: object, *, environment_id: str, state: EnvironmentState | None
    ) -> timedelta:
        return self._target(environment, environment_id, state, "op-" + uuid4().hex).keepalive_horizon

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(_release(None, self._owned), name="environment-provider-close")
        await asyncio.shield(self._close_task)

    async def scrape_target(
        self, environment: object, *, environment_id: str, state: EnvironmentState | None
    ) -> ScrapeTarget:
        target = self._target(environment, environment_id, state, "op-" + uuid4().hex)
        try:
            target.validate_management()
            return await target.scrape_target()
        finally:
            await _release(target, None)

    async def observe_device(
        self,
        environment: object,
        *,
        environment_id: str,
        state: EnvironmentState | None,
        credential_cache: ManagedCredentialCache | None = None,
    ) -> DeviceObservation:
        # Observation has no lifecycle operation or publication. Preserve its errors
        # and cancellation rather than wrapping them as uncertain mutations.
        target = self._target(environment, environment_id, state, "op-" + uuid4().hex)
        try:
            target.validate_management()
            return await target.observe_device(credential_cache=credential_cache)
        finally:
            await _release(target, None)

    async def desktop_access(
        self,
        environment: object,
        *,
        environment_id: str,
        state: EnvironmentState | None,
        operation_id: str,
        request: Request,
    ) -> Reply:
        async with self._operation(environment, environment_id, state, operation_id) as target:
            return await target.desktop_access(request)
