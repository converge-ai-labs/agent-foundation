"""Common native definition, construction and local operation wiring."""

import hashlib
from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.models import (
    ENVIRONMENT_ACTION_DISPATCH,
    FILE_ACTIONS,
    EnvironmentAction,
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentMountDescriptor,
    EnvironmentOperationFamily,
    EnvironmentPermissionSet,
    EnvironmentState,
    decode_target_state,
)
from a13n_harness.environment.operations import EnvironmentOperations
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.providers.environment._backend import BackendReference, ExecutionBackend
from a13n_harness.providers.environment._backend_factory import BackendFactory
from a13n_harness.providers.environment._guest_files import GuestFiles
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.native.commands import Execute, NativeCommands
from a13n_harness.providers.environment.native.configuration import CommandConfiguration, NamedTargetState, TargetState
from a13n_harness.providers.environment.native.errors import failure

_DELETE_POLL_SECONDS = 0.5


def descriptor(
    config: CommandConfiguration, identity: str | None = None, *, incarnation: str | None = None
) -> EnvironmentDescriptor:
    actions = FILE_ACTIONS | {EnvironmentAction.SHELL_EXEC}
    generation = (
        "generation-" + hashlib.sha256((incarnation or identity).encode()).hexdigest()[:24]
        if identity
        else "unprepared"
    )
    return EnvironmentDescriptor(
        generation=generation,
        backing_identity=identity,
        operation_families=frozenset(ENVIRONMENT_ACTION_DISPATCH[a].family for a in actions),
        permissions=EnvironmentPermissionSet(operations=frozenset(actions)),
        mounts=(EnvironmentMountDescriptor(name="root", path="/"),),
        limits={"max_value_bytes": config.max_file_bytes, "max_output_bytes": config.max_output_bytes},
    )


class TargetConfiguration(Protocol):
    """Identity and lifecycle bounds shared by native resource references."""

    @property
    def fingerprint(self) -> str: ...

    @property
    def request_timeout_seconds(self) -> float: ...


class NativeReference[C: TargetConfiguration, S: TargetState](BackendReference):
    """Shared target identity and read-only validation; no lifecycle or execution API."""

    def __init__(
        self,
        key: str,
        config: C,
        environment_id: str,
        state: EnvironmentState | None,
        *,
        state_model: type[S],
    ):
        super().__init__(state)
        self._key = key
        self.config = config
        self._environment_id = environment_id
        self.target = decode_target_state(key, state, state_model, fingerprint=config.fingerprint)
        self.owner = self.target.environment_id if self.target else environment_id
        self.name = "a13n-" + hashlib.sha256(self.owner.encode()).hexdigest()[:32]

    @property
    def provider_key(self) -> str:
        return self._key

    @property
    def environment_id(self) -> str:
        return self._environment_id

    @property
    def labels(self) -> dict[str, str]:
        return {"a13n_environment": self.owner, "a13n_configuration": self.config.fingerprint}

    def validate_labels(self, labels: dict[str, str]) -> None:
        if any(labels.get(key) != value for key, value in self.labels.items()):
            raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)

    def require_target(self) -> None:
        if self.target is None:
            raise failure(self.provider_key, "provider_state_invalid", Category.INVALID)

    @abstractmethod
    def observe(self, state: S) -> None: ...

    @abstractmethod
    async def status(self) -> Literal["running", "stopped", "absent"]: ...

    @abstractmethod
    async def close_transport(self) -> None: ...


class NativeExecution[C: CommandConfiguration, S: TargetState](NativeReference[C, S], ExecutionBackend):
    def __init__(
        self, key: str, config: C, environment_id: str, state: EnvironmentState | None, *, state_model: type[S]
    ):
        super().__init__(key, config, environment_id, state, state_model=state_model)
        self._descriptor = descriptor(config)
        self._availability = EnvironmentAvailability(status="preparing")
        self._operations = EnvironmentOperations()
        self.commands: NativeCommands | None = None

    def observe(self, state: S) -> None:
        if state != self.target:
            raise failure(self.provider_key, "provider_target_changed", Category.CONFLICT)

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self._descriptor

    @property
    def availability(self) -> EnvironmentAvailability:
        return self._availability

    @property
    def operations(self) -> EnvironmentOperations:
        return self._operations

    async def open_operations(
        self, execute: Execute, identity: str, execution_id: str, *, incarnation: str | None = None
    ) -> None:
        self._descriptor = descriptor(self.config, identity, incarnation=incarnation)
        if self.commands:
            self.commands.closed = True
        self.commands = NativeCommands(execute, self.config, self.descriptor.generation, execution_id)
        files = GuestFiles(self.commands)
        self._operations = EnvironmentOperations(files=files, shell=self.commands)
        self._availability = EnvironmentAvailability(
            status="available", ready_families=self.descriptor.operation_families
        )

    async def check_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        if operations - self.descriptor.operation_families:
            raise EnvironmentError("Unsupported operation family.", code="environment_unsupported")
        if self.commands is None:
            raise EnvironmentError("Native environment is unavailable.", code="environment_unavailable")
        if await self.status() != "running":
            self._availability = EnvironmentAvailability(status="unavailable")
            raise EnvironmentError("Native environment is unavailable.", code="environment_unavailable")

    async def close(self) -> None:
        if self.commands:
            self.commands.closed = True
        self._operations = EnvironmentOperations()
        self._availability = EnvironmentAvailability(status="unavailable")
        await self.close_transport()


@dataclass(frozen=True, slots=True)
class NativeRuntime:
    configuration: BaseModel
    credential: BaseModel


def native_definition[C: BaseModel, K: BaseModel, E: CommandConfiguration](
    *,
    type: str,
    display_name: str,
    configuration_model: type[C],
    credential_model: type[K],
    environment_model: type[E],
    state_model: type[TargetState],
    execution_type: Callable[[E, str, EnvironmentState | None, NativeRuntime], ExecutionBackend],
    describe_environment: Callable[[E], EnvironmentDescriptor] | None = None,
) -> EnvironmentProviderDefinition[C, K, E, NativeRuntime]:
    async def runtime(*, configuration: C, credential: K | None) -> NativeRuntime:
        if credential is None:
            raise ValueError(f"the {type!r} Environment Provider requires a credential")
        return NativeRuntime(configuration, credential)

    def describe(configuration: E) -> EnvironmentDescriptor:
        return describe_environment(configuration) if describe_environment is not None else descriptor(configuration)

    def identity(*, configuration: E, state: EnvironmentState | None) -> str | None:
        value = decode_target_state(type, state, state_model, fingerprint=configuration.fingerprint)
        if value is None:
            raise failure(type, "provider_state_required", Category.INVALID)
        if isinstance(value, NamedTargetState):
            return value.backing_id
        return value.target_id

    def construct_execution(
        *,
        configuration: E,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime | None,
        session_recovery: EnvdSessionRecovery | None = None,
    ) -> ExecutionBackend:
        if runtime is None:
            raise TypeError("Native execution construction requires a runtime")
        return execution_type(configuration, environment_id, state, runtime)

    factory = BackendFactory(
        key=type,
        environment_model=environment_model,
        execution=construct_execution,
        describe=describe,
        runtime_factory=runtime,
        target_identity=identity,
    )
    return EnvironmentProviderDefinition(
        type=type,
        display_name=display_name,
        configuration_model=configuration_model,
        credential_model=credential_model,
        environment_model=environment_model,
        connector_factory=factory.connector,
        describe_environment=describe,
        target_identity=identity,
    )
