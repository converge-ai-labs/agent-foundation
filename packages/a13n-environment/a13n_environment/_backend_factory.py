"""Built-in Provider/Connector composition with separate client ownership."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol
from uuid import uuid4

import anyio
from pydantic import BaseModel

from ._backend import BackendTarget
from .definition import TargetIdentity, _no_target_identity
from .errors import EnvironmentManagementCancelled, EnvironmentManagementError, EnvironmentProviderError, provider_error
from .errors import EnvironmentProviderErrorCategory as Category
from .errors import EnvironmentProviderOutcomeCertainty as Certainty
from .execution import EnvironmentConnector, EnvironmentExecution
from .management import ClosableRuntime, EnvironmentProvider, EnvironmentStatus
from .models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationFamily,
    EnvironmentState,
)
from .operations import EnvironmentOperations


class RuntimeFactory[C: BaseModel, K: BaseModel, R](Protocol):
    def __call__(self, *, configuration: C, credential: K | None) -> Awaitable[R]: ...


class TargetFactory[E: BaseModel, R](Protocol):
    def __call__(
        self,
        *,
        configuration: E,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: R | None,
        operation_id: str,
    ) -> BackendTarget: ...


async def _release(target: BackendTarget | None, runtime: ClosableRuntime | None) -> None:
    """Attempt every owned cleanup even when the first one fails."""
    with anyio.CancelScope(shield=True):
        try:
            if target is not None:
                await target.close()
        finally:
            if runtime is not None:
                await runtime.close()


@dataclass(frozen=True, slots=True)
class BackendFactory[C: BaseModel, K: BaseModel, E: BaseModel, R]:
    key: str
    environment_model: type[E]
    target: TargetFactory[E, R]
    describe: Callable[[E], EnvironmentDescriptor]
    runtime_factory: RuntimeFactory[C, K, R] | None = None
    target_identity: TargetIdentity[E] = _no_target_identity

    async def acquire(
        self, configuration: C, credential: K | None, borrowed: R | None
    ) -> tuple[R | None, ClosableRuntime | None]:
        if borrowed is not None or self.runtime_factory is None:
            return borrowed, None
        acquired = await self.runtime_factory(configuration=configuration, credential=credential)
        return acquired, acquired if isinstance(acquired, ClosableRuntime) else None

    async def provider(self, *, configuration: C, credential: K | None, runtime: R | None) -> EnvironmentProvider[E]:
        acquired, owned = await self.acquire(configuration, credential, runtime)
        return BackendProvider(self, configuration, credential, runtime, acquired, owned)

    def connector(
        self,
        *,
        configuration: C,
        credential: K | None,
        environment: E,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: R | None,
    ) -> EnvironmentConnector:
        self.target_identity(configuration=environment, state=state)
        return BackendConnector(self, configuration, credential, environment, environment_id, state, runtime)


@dataclass(frozen=True, slots=True)
class BackendConnector[C: BaseModel, K: BaseModel, E: BaseModel, R](EnvironmentConnector):
    factory: BackendFactory[C, K, E, R]
    configuration: C = field(repr=False)
    credential: K | None = field(repr=False)
    environment: E
    _environment_id: str
    _state: EnvironmentState | None
    runtime: R | None = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "configuration", self.configuration.model_copy(deep=True))
        object.__setattr__(
            self, "credential", self.credential.model_copy(deep=True) if self.credential is not None else None
        )
        object.__setattr__(self, "environment", self.environment.model_copy(deep=True))
        object.__setattr__(self, "_state", self._state.model_copy(deep=True) if self._state is not None else None)

    @property
    def provider_key(self) -> str:
        return self.factory.key

    @property
    def environment_id(self) -> str:
        return self._environment_id

    @property
    def state(self) -> EnvironmentState | None:
        return self._state.model_copy(deep=True) if self._state is not None else None

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self.factory.describe(self.environment)

    async def open(self) -> EnvironmentExecution:
        runtime, owned = await self.factory.acquire(self.configuration, self.credential, self.runtime)
        target: BackendTarget | None = None
        try:
            target = self.factory.target(
                configuration=self.environment,
                environment_id=self.environment_id,
                state=self.state,
                runtime=runtime,
                operation_id="op-" + uuid4().hex,
            )
            execution_id = "exec-" + uuid4().hex
            await target.open(execution_id=execution_id)
            await target.check_ready(target.descriptor.operation_families)
            # Opening may observe a target, but it cannot change the Host's reference.
            if target.state != self.state:
                raise provider_error(self.provider_key, "provider_target_changed", Category.CONFLICT)
            return BackendExecution(target, execution_id, self.state, owned)
        except BaseException as error:
            try:
                await _release(target, owned)
            except BaseException as cleanup_error:
                error.add_note(f"Environment opening cleanup also failed: {cleanup_error!r}")
            raise


class BackendExecution(EnvironmentExecution):
    def __init__(
        self, target: BackendTarget, execution_id: str, state: EnvironmentState | None, runtime: ClosableRuntime | None
    ):
        self._target = target
        self._execution_id = execution_id
        self._state = state
        self._runtime = runtime
        self._close_task: asyncio.Task[None] | None = None

    @property
    def provider_key(self) -> str:
        return self._target.provider_key

    @property
    def environment_id(self) -> str:
        return self._target.environment_id

    @property
    def execution_id(self) -> str:
        return self._execution_id

    @property
    def state(self) -> EnvironmentState | None:
        return self._state.model_copy(deep=True) if self._state is not None else None

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self._target.descriptor

    @property
    def availability(self) -> EnvironmentAvailability:
        if self._close_task is not None:
            return EnvironmentAvailability(status="unavailable")
        return self._target.availability

    @property
    def operations(self) -> EnvironmentOperations:
        if self._close_task is not None:
            raise EnvironmentError("Environment execution is closed.", code="environment_closed")
        return self._target.operations

    async def check_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        if self._close_task is not None:
            raise EnvironmentError("Environment execution is closed.", code="environment_closed")
        await self._target.check_ready(operations)
        if self._close_task is not None:
            raise EnvironmentError("Environment execution is closed.", code="environment_closed")

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                _release(self._target, self._runtime), name="environment-execution-close"
            )
        await asyncio.shield(self._close_task)


class BackendProvider[C: BaseModel, K: BaseModel, E: BaseModel, R](EnvironmentProvider[E]):
    def __init__(
        self,
        factory: BackendFactory[C, K, E, R],
        configuration: C,
        credential: K | None,
        borrowed: R | None,
        runtime: R | None,
        owned: ClosableRuntime | None,
    ) -> None:
        self._factory = factory
        self._configuration = configuration
        self._credential = credential
        self._borrowed = borrowed
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
    ) -> BackendTarget:
        return self._factory.target(
            configuration=self._recipe(environment),
            environment_id=environment_id,
            state=state,
            runtime=self._runtime,
            operation_id=operation_id,
        )

    @asynccontextmanager
    async def _operation(
        self, environment: object, environment_id: str, state: EnvironmentState | None, operation_id: str
    ) -> AsyncIterator[BackendTarget]:
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
        self, environment: object, *, environment_id: str, operation_id: str, state: EnvironmentState | None = None
    ) -> EnvironmentState | None:
        async with self._operation(environment, environment_id, state, operation_id) as target:
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

    def execution_connector(
        self, environment: object, *, environment_id: str, state: EnvironmentState | None
    ) -> EnvironmentConnector:
        return self._factory.connector(
            configuration=self._configuration,
            credential=self._credential,
            environment=self._recipe(environment),
            environment_id=environment_id,
            state=state,
            runtime=self._borrowed,
        )

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(_release(None, self._owned), name="environment-provider-close")
        await asyncio.shield(self._close_task)
