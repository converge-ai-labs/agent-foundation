"""E2B target lifecycle with Host-authoritative state and native operations."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, SecretStr

from ...authentication import Authentication, CredentialMode
from .._guest_files import GuestFiles
from .._guest_ports import GuestPorts
from ..definition import EnvironmentProviderDefinition
from ..errors import EnvironmentProviderErrorCategory as Category
from ..management import Environment
from ..models import (
    ENVIRONMENT_ACTION_DISPATCH,
    FILE_EXECUTION_ACTIONS,
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
from ..operations import EnvironmentOperations
from .commands import GuestCommands
from .configuration import (
    PROVIDER_KEY,
    E2BConnectionConfiguration,
    E2BCredential,
    E2BEnvironmentConfiguration,
    E2BProviderStateData,
)
from .errors import provider_error, sdk_errors
from .processes import E2BProcesses

if TYPE_CHECKING:
    from e2b import AsyncSandbox
    from e2b.sandbox.sandbox_api import SandboxInfo


class E2BEnvironment(Environment):
    recover_on_unavailable = True

    def __init__(
        self,
        configuration: E2BEnvironmentConfiguration,
        *,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: E2BProviderRuntime,
        allow_create: bool = True,
        operation_id: str | None = None,
    ) -> None:
        super().__init__(state)
        self._configuration = configuration
        self._environment_id = environment_id
        self._runtime = runtime
        self._managed = allow_create
        self._operation_id = operation_id
        self._state = decode_target_state(
            PROVIDER_KEY, state, E2BProviderStateData, fingerprint=configuration.fingerprint
        )
        if self._state is not None and self._managed and self._state.environment_id != environment_id:
            raise provider_error("provider_target_conflict", Category.CONFLICT)
        self._native_id = self._state.environment_id if self._state else environment_id
        self._descriptor = descriptor(configuration)
        self._availability = EnvironmentAvailability(status="preparing")
        self._operations = EnvironmentOperations()
        self._commands: GuestCommands | None = None
        self._processes: E2BProcesses | None = None

    @property
    def provider_key(self) -> str:
        return PROVIDER_KEY

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

    def _options(self):
        from e2b.connection_config import ApiParams

        return ApiParams(
            api_key=self._runtime.api_key.get_secret_value(),
            domain=self._runtime.domain,
            api_url=self._runtime.api_url or f"https://api.{self._runtime.domain}",
            request_timeout=self._configuration.request_timeout_seconds,
        )

    def _remember(self, sandbox_id: str) -> None:
        self._state = E2BProviderStateData(
            sandbox_id=sandbox_id,
            environment_id=self._native_id,
            configuration_fingerprint=self._configuration.fingerprint,
        )
        self._cache_state(
            EnvironmentState(provider_key=PROVIDER_KEY, state_version="1", state=self._state.model_dump(mode="json"))
        )

    def _validate_target(self, target: SandboxInfo) -> None:
        if (
            target.metadata.get("a13n_harness.providers.environment") != self._native_id
            or target.metadata.get("a13n_configuration") != self._configuration.fingerprint
        ):
            raise provider_error("provider_target_conflict", Category.CONFLICT)
        if self._state is not None and target.sandbox_id != self._state.sandbox_id:
            raise provider_error("provider_target_conflict", Category.CONFLICT)

    async def _inspect(self) -> SandboxInfo | None:
        from e2b import AsyncSandbox
        from e2b.exceptions import SandboxNotFoundException
        from e2b.sandbox.sandbox_api import SandboxQuery

        with sdk_errors():
            if self._state is not None:
                try:
                    target = await AsyncSandbox.get_info(self._state.sandbox_id, **self._options())
                except SandboxNotFoundException:
                    return None
            else:
                paginator = AsyncSandbox.list(
                    query=SandboxQuery(metadata={"a13n_harness.providers.environment": self._native_id}),
                    limit=2,
                    **self._options(),
                )
                matches = await paginator.next_items()
                if len(matches) > 1 or paginator.has_next:
                    raise provider_error("provider_target_conflict", Category.CONFLICT)
                if not matches:
                    return None
                target = matches[0]
            self._validate_target(target)
            self._remember(target.sandbox_id)
            return target

    async def _prepare(self, *, mount_id: str) -> None:
        from e2b import AsyncSandbox

        target = await self._inspect()
        config = self._configuration
        with sdk_errors(mutation=True):
            if target is None:
                if not self._managed:
                    raise provider_error("provider_target_missing", Category.MISSING)
                metadata = {
                    "a13n_harness.providers.environment": self._native_id,
                    "a13n_configuration": config.fingerprint,
                }
                if self._operation_id:
                    metadata["a13n_operation"] = self._operation_id
                sandbox = await AsyncSandbox.create(
                    template=config.template,
                    timeout=config.timeout_seconds,
                    metadata=metadata,
                    allow_internet_access=config.allow_internet_access,
                    secure=True,
                    lifecycle={"on_timeout": "kill", "auto_resume": False},
                    **self._options(),
                )
                self._remember(sandbox.sandbox_id)
            else:
                sandbox = await AsyncSandbox.connect(
                    target.sandbox_id, timeout=config.timeout_seconds, **self._options()
                )
        await self._open_operations(sandbox, mount_id)

    async def _open_operations(self, sandbox: AsyncSandbox, mount_id: str) -> None:
        commands = GuestCommands(sandbox, self._configuration)
        commands.generation = "generation-" + hashlib.sha256(sandbox.sandbox_id.encode()).hexdigest()[:24]
        commands.mount_id = mount_id
        files = GuestFiles(commands)
        await files.stat("/")
        processes = self._processes
        if processes is not None:
            await processes.disconnect()
        if processes is None or processes.commands.generation != commands.generation:
            processes = E2BProcesses(commands, self.environment_id)
        else:
            processes.commands = commands
        if self._commands is not None:
            self._commands.closed = True
        self._commands = commands
        self._processes = processes
        self._operations = EnvironmentOperations(
            files=files,
            shell=processes,
            processes=processes,
            ports=GuestPorts(commands),
        )
        self._descriptor = descriptor(self._configuration, commands.generation, sandbox.sandbox_id)
        self._availability = EnvironmentAvailability(
            status="available", ready_families=self._descriptor.operation_families
        )

    def _bind_mount(self, mount_id: str) -> None:
        if self._commands is not None:
            self._commands.mount_id = mount_id

    async def _ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        if operations - self.descriptor.operation_families:
            raise EnvironmentError("E2B operation family is unsupported.", code="environment_unsupported")
        if self._commands is None:
            raise EnvironmentError("E2B is unavailable.", code="environment_unavailable")
        with sdk_errors():
            ready = await self._commands.sandbox.is_running(request_timeout=self._configuration.request_timeout_seconds)
        if not ready:
            self._availability = EnvironmentAvailability(status="unavailable")
            raise EnvironmentError("E2B is unavailable.", code="environment_unavailable")

    async def reconcile(self) -> Literal["running", "stopped", "absent"]:
        target = await self._inspect()
        if target is None:
            return "absent"
        return "stopped" if target.state.value == "paused" else "running"

    async def _stop(self) -> None:
        from e2b import AsyncSandbox

        target = await self._inspect()
        if target is not None and target.state.value != "paused":
            with sdk_errors(mutation=True):
                await AsyncSandbox.pause(target.sandbox_id, keep_memory=True, **self._options())

    @property
    def keepalive_horizon(self) -> timedelta:
        return min(super().keepalive_horizon, timedelta(seconds=self._configuration.timeout_seconds))

    async def keepalive(self, *, deadline: datetime, operation_id: str) -> datetime:
        from e2b import AsyncSandbox

        if deadline.tzinfo is None or not operation_id:
            raise provider_error("provider_keepalive_invalid", Category.INVALID)
        target = await self._inspect()
        if target is None:
            raise provider_error("provider_target_missing", Category.MISSING)
        if target.state.value != "running":
            raise provider_error("provider_target_stopped", Category.CONFLICT)
        if target.end_at >= deadline:
            return target.end_at
        seconds = math.ceil((deadline - datetime.now(UTC)).total_seconds())
        if seconds > self._configuration.timeout_seconds:
            raise provider_error("provider_keepalive_limit", Category.UNSUPPORTED)
        with sdk_errors(mutation=True):
            await AsyncSandbox.set_timeout(target.sandbox_id, max(1, seconds), **self._options())
        refreshed = await self._inspect()
        if refreshed is None or refreshed.state.value != "running" or refreshed.end_at < deadline:
            raise provider_error("provider_keepalive_unsatisfied", Category.UNAVAILABLE)
        return refreshed.end_at

    async def _destroy(self) -> None:
        from e2b import AsyncSandbox

        target = await self._inspect()
        if target is not None:
            with sdk_errors(mutation=True):
                await AsyncSandbox.kill(target.sandbox_id, **self._options())
        self._state = None

    async def _close(self) -> None:
        try:
            if self._processes is not None:
                await self._processes.close()
        finally:
            if self._commands is not None:
                self._commands.closed = True
            self._commands = None
            self._processes = None
            self._operations = EnvironmentOperations()
            self._availability = EnvironmentAvailability(status="unavailable")


def descriptor(
    configuration: E2BEnvironmentConfiguration, generation: str = "unprepared", identity: str | None = None
) -> EnvironmentDescriptor:
    actions = {action for action in FILE_EXECUTION_ACTIONS if not action.value.startswith("environment.state.")}
    actions -= {EnvironmentAction.PROCESS_SIGNAL, EnvironmentAction.OUTPUT_READ, EnvironmentAction.OUTPUT_RELEASE}
    return EnvironmentDescriptor(
        generation=generation,
        backing_identity=identity,
        operation_families=frozenset(ENVIRONMENT_ACTION_DISPATCH[action].family for action in actions),
        permissions=EnvironmentPermissionSet(operations=frozenset(actions)),
        mounts=(EnvironmentMountDescriptor(name="root", path="/"),),
        limits={
            "max_value_bytes": configuration.max_file_bytes,
            "max_observation_bytes": configuration.max_observation_bytes,
            "max_active_observations": configuration.max_active_observations,
            "max_retained_output_bytes": configuration.max_retained_output_bytes,
        },
    )


@dataclass(frozen=True, slots=True)
class E2BProviderRuntime:
    api_key: SecretStr = field(repr=False)
    domain: str = "e2b.dev"
    api_url: str | None = None

    def __post_init__(self) -> None:
        E2BCredential(api_key=self.api_key)
        E2BConnectionConfiguration(domain=self.domain, api_url=self.api_url)


async def _runtime(*, configuration: BaseModel, credential: BaseModel | None) -> E2BProviderRuntime:
    if not isinstance(configuration, E2BConnectionConfiguration) or not isinstance(credential, E2BCredential):
        raise TypeError("E2B requires E2BConnectionConfiguration and E2BCredential")
    return E2BProviderRuntime(
        api_key=credential.api_key,
        domain=configuration.domain,
        api_url=configuration.api_url,
    )


def _describe(configuration: E2BEnvironmentConfiguration) -> EnvironmentDescriptor:
    if not isinstance(configuration, E2BEnvironmentConfiguration):
        raise TypeError("E2B requires E2BEnvironmentConfiguration")
    return descriptor(configuration)


def _identity(*, configuration: E2BEnvironmentConfiguration, state: EnvironmentState | None) -> str | None:
    if not isinstance(configuration, E2BEnvironmentConfiguration):
        raise TypeError("E2B requires E2BEnvironmentConfiguration")
    data = decode_target_state(PROVIDER_KEY, state, E2BProviderStateData, fingerprint=configuration.fingerprint)
    return data.sandbox_id if data is not None else None


def _construct(
    *,
    configuration: E2BEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: E2BProviderRuntime | None,
    operation_id: str,
    allow_create: bool,
) -> Environment:
    if not isinstance(configuration, E2BEnvironmentConfiguration) or runtime is None:
        raise TypeError("E2B requires E2BEnvironmentConfiguration and E2BProviderRuntime")
    return E2BEnvironment(
        configuration,
        environment_id=environment_id,
        state=state,
        runtime=runtime,
        operation_id=operation_id,
        allow_create=allow_create,
    )


E2B = EnvironmentProviderDefinition(
    type=PROVIDER_KEY,
    display_name="E2B",
    configuration_model=E2BConnectionConfiguration,
    credential_model=E2BCredential,
    environment_model=E2BEnvironmentConfiguration,
    construct=_construct,
    describe_environment=_describe,
    target_identity=_identity,
    authentication=Authentication(mode=CredentialMode.required),
    setup_url="https://e2b.dev/dashboard?tab=keys",
    setup_label="E2B API keys",
    supports_stop=True,
    supports_destroy=True,
    runtime_factory=_runtime,
    requires_keepalive=True,
)
