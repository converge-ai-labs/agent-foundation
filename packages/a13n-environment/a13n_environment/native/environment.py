"""Common local operation wiring; lifecycle remains in the native adapters."""

import asyncio
import hashlib
from abc import abstractmethod
from collections.abc import Awaitable, Callable

from .._guest_files import GuestFiles
from ..errors import EnvironmentProviderError
from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import EnvironmentProviderOutcomeCertainty as Certainty
from ..management import Environment
from ..models import (
    ENVIRONMENT_ACTION_DISPATCH,
    EnvironmentAction,
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentMountDescriptor,
    EnvironmentOperationFamily,
    EnvironmentPermissionSet,
    EnvironmentState,
)
from ..operations import EnvironmentOperations
from .commands import Execute, NativeCommands
from .configuration import CommandConfiguration, TargetState
from .errors import failure


def descriptor(
    config: CommandConfiguration, identity: str | None = None, *, incarnation: str | None = None
) -> EnvironmentDescriptor:
    actions = {action for action in EnvironmentAction if action.value.startswith("environment.file.")}
    if config.read_only:
        actions &= {
            EnvironmentAction.FILE_STAT,
            EnvironmentAction.FILE_READ_TEXT,
            EnvironmentAction.FILE_READ_BYTES,
            EnvironmentAction.FILE_LIST,
            EnvironmentAction.FILE_QUERY,
            EnvironmentAction.FILE_SEARCH_TEXT,
            EnvironmentAction.FILE_COPY_SOURCE,
        }
    else:
        actions.add(EnvironmentAction.SHELL_EXEC)
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
        mounts=(EnvironmentMountDescriptor(name="root", path="/", read_only=config.read_only),),
        limits={"max_value_bytes": config.max_file_bytes, "max_output_bytes": config.max_output_bytes},
    )


def decode_state[S: TargetState](
    key: str, config: CommandConfiguration, state: EnvironmentState | None, model: type[S]
) -> S | None:
    if state is None:
        return None
    if state.provider_key != key or state.state_version != "1":
        raise failure(key, "provider_state_invalid", Category.INVALID)
    try:
        value = model.model_validate(state.state)
    except ValueError:
        raise failure(key, "provider_state_invalid", Category.INVALID) from None
    if value.configuration_fingerprint != config.fingerprint:
        raise failure(key, "provider_target_conflict", Category.CONFLICT)
    return value


class NativeEnvironment[C: CommandConfiguration, S: TargetState](Environment):
    recover_on_unavailable = True

    def __init__(
        self,
        key: str,
        config: C,
        environment_id: str,
        state: EnvironmentState | None,
        *,
        managed: bool,
        state_model: type[S],
    ):
        super().__init__(state)
        self._key = key
        self.config = config
        self._environment_id = environment_id
        self.managed = managed
        self.target = decode_state(key, config, state, state_model)
        if self.target is not None and managed and self.target.environment_id != environment_id:
            raise failure(key, "provider_target_conflict", Category.CONFLICT)
        if not managed and self.target is None:
            raise failure(key, "provider_state_invalid", Category.INVALID)
        self.owner = self.target.environment_id if self.target else environment_id
        self.name = "a13n-" + hashlib.sha256(self.owner.encode()).hexdigest()[:32]
        self._descriptor = descriptor(config)
        self._availability = EnvironmentAvailability(status="preparing")
        self._operations = EnvironmentOperations()
        self.commands: NativeCommands | None = None

    @property
    def provider_key(self) -> str:
        return self._key

    @property
    def environment_id(self) -> str:
        return self._environment_id

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self._descriptor

    @property
    def availability(self) -> EnvironmentAvailability:
        return self._availability

    @property
    def operations(self) -> EnvironmentOperations:
        return self._operations

    @property
    def labels(self) -> dict[str, str]:
        return {"a13n_environment": self.owner, "a13n_configuration": self.config.fingerprint}

    def remember(self, state: S) -> None:
        self.target = state
        self._cache_state(
            EnvironmentState(provider_key=self.provider_key, state_version="1", state=state.model_dump(mode="json"))
        )

    def validate_labels(self, labels: dict[str, str]) -> None:
        if self.managed and any(labels.get(key) != value for key, value in self.labels.items()):
            raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)

    async def open_operations(
        self, execute: Execute, identity: str, mount_id: str, *, incarnation: str | None = None
    ) -> None:
        self._descriptor = descriptor(self.config, identity, incarnation=incarnation)
        if self.commands:
            self.commands.closed = True
        self.commands = NativeCommands(execute, self.config, self.descriptor.generation, mount_id)
        files = GuestFiles(self.commands)
        await files.stat("/")
        self._operations = EnvironmentOperations(files=files, shell=None if self.config.read_only else self.commands)
        self._availability = EnvironmentAvailability(
            status="available", ready_families=self.descriptor.operation_families
        )

    def _bind_mount(self, mount_id: str) -> None:
        if self.commands:
            self.commands.mount_id = mount_id

    async def _ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        if operations - self.descriptor.operation_families:
            raise EnvironmentError("Unsupported operation family.", code="environment_unsupported")
        if self.commands is None:
            raise EnvironmentError("Native environment is unavailable.", code="environment_unavailable")
        try:
            await self.commands.helper("print('{}')", {})
        except EnvironmentProviderError as error:
            if error.category == Category.MISSING:
                raise EnvironmentError("Native environment is unavailable.", code="environment_unavailable") from None
            raise

    async def confirm_deleted(self, inspect: Callable[[], Awaitable[object | None]]) -> None:
        """A delete acknowledgement alone does not establish terminal target absence."""
        try:
            async with asyncio.timeout(self.config.request_timeout_seconds):
                while await inspect() is not None:
                    await asyncio.sleep(0.5)
        except (TimeoutError, EnvironmentProviderError) as error:
            raise failure(
                self.provider_key,
                "provider_delete_unconfirmed",
                Category.UNKNOWN_OUTCOME,
                certainty=Certainty.UNKNOWN,
            ) from error

    async def _close(self) -> None:
        if self.commands:
            self.commands.closed = True
        self._operations = EnvironmentOperations()
        self._availability = EnvironmentAvailability(status="unavailable")
        await self.close_transport()

    @abstractmethod
    async def close_transport(self) -> None: ...
