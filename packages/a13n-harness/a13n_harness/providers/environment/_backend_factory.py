"""Built-in Provider/Connector composition with separate client ownership."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import provider_error
from a13n_harness.environment.execution import EnvironmentConnector, EnvironmentExecution
from a13n_harness.environment.models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationFamily,
    EnvironmentState,
)
from a13n_harness.environment.operations import EnvironmentOperations
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.providers.environment._backend import ExecutionBackend
from a13n_harness.providers.environment._runtime import ClosableRuntime, RuntimeFactory, _release
from a13n_harness.providers.environment.definition import TargetIdentity, _no_target_identity


class ExecutionFactory[E: BaseModel, R](Protocol):
    def __call__(
        self,
        *,
        configuration: E,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: R | None,
        session_recovery: EnvdSessionRecovery | None = None,
    ) -> ExecutionBackend: ...


@dataclass(frozen=True, slots=True)
class BackendFactory[C: BaseModel, K: BaseModel, E: BaseModel, R]:
    key: str
    environment_model: type[E]
    execution: ExecutionFactory[E, R]
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

    def connector(
        self,
        *,
        configuration: C,
        credential: K | None,
        environment: E,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: R | None,
        session_recovery: EnvdSessionRecovery | None = None,
    ) -> EnvironmentConnector:
        self.target_identity(configuration=environment, state=state)
        return BackendConnector(
            self, configuration, credential, environment, environment_id, state, runtime, session_recovery
        )


@dataclass(frozen=True, slots=True)
class BackendConnector[C: BaseModel, K: BaseModel, E: BaseModel, R](EnvironmentConnector):
    factory: BackendFactory[C, K, E, R]
    configuration: C = field(repr=False)
    credential: K | None = field(repr=False)
    environment: E
    _environment_id: str
    _state: EnvironmentState | None
    runtime: R | None = field(repr=False)
    session_recovery: EnvdSessionRecovery | None = field(default=None, repr=False)

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
        target: ExecutionBackend | None = None
        try:
            target = self.factory.execution(
                configuration=self.environment,
                environment_id=self.environment_id,
                state=self.state,
                runtime=runtime,
                session_recovery=self.session_recovery,
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
        self,
        target: ExecutionBackend,
        execution_id: str,
        state: EnvironmentState | None,
        runtime: ClosableRuntime | None,
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
