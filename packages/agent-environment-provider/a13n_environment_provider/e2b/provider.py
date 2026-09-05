"""E2B target lifecycle with Host-authoritative state and native operations."""

from __future__ import annotations

import hashlib
import math
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from ..errors import EnvironmentProviderErrorCategory as Category
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
from .commands import GuestCommands
from .configuration import PROVIDER_KEY, E2BProviderConfiguration, E2BProviderStateData
from .errors import provider_error, sdk_errors
from .files import E2BFiles
from .output import E2BOutputs
from .ports import E2BPorts
from .processes import E2BProcesses
from .runtime import E2BProviderRuntime

if TYPE_CHECKING:
    from e2b import AsyncSandbox
    from e2b.sandbox.sandbox_api import SandboxInfo


class E2BEnvironment(Environment):
    recover_on_unavailable = True

    def __init__(
        self,
        configuration: E2BProviderConfiguration,
        *,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: E2BProviderRuntime,
    ) -> None:
        super().__init__(state)
        self._configuration = configuration
        self._environment_id = environment_id
        self._runtime = runtime
        self._state = decode_state(configuration, state)
        if self._state is not None and runtime.managed and self._state.environment_id != environment_id:
            raise provider_error("provider_target_conflict", Category.CONFLICT)
        self._native_id = self._state.environment_id if self._state else environment_id
        self._descriptor = descriptor(configuration)
        self._availability = EnvironmentAvailability(status="preparing")
        self._operations = EnvironmentOperations()
        self._commands: GuestCommands | None = None
        self._processes: E2BProcesses | None = None
        self._process_owner = "scope-" + secrets.token_hex(12)

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
            target.metadata.get("a13n_environment") != self._native_id
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
                    query=SandboxQuery(metadata={"a13n_environment": self._native_id}), limit=2, **self._options()
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

    async def _prepare(
        self, *, thread_id: str, run_id: str, agent_instance_id: str, mount_id: str, host_refs: Mapping[str, str]
    ) -> None:
        from e2b import AsyncSandbox

        del thread_id, run_id, agent_instance_id, host_refs
        target = await self._inspect()
        config = self._configuration
        with sdk_errors(mutation=True):
            if target is None:
                if not self._runtime.managed:
                    raise provider_error("provider_target_missing", Category.MISSING)
                metadata = {"a13n_environment": self._native_id, "a13n_configuration": config.fingerprint}
                if self._runtime.operation_id:
                    metadata["a13n_operation"] = self._runtime.operation_id
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
        root_token = hashlib.sha256(f"{self._native_id}:{self._configuration.fingerprint}".encode()).hexdigest()[:24]
        commands = GuestCommands(sandbox, self._configuration, f"/tmp/a13n-{root_token}")
        prepared = await commands.process("prepare", {}, mutation=True)
        boot_id = prepared.get("boot_id")
        if not isinstance(boot_id, str) or not boot_id:
            raise EnvironmentError("E2B boot identity is missing.", code="environment_provider_failure")
        commands.boot_id = boot_id
        commands.generation = (
            "generation-" + hashlib.sha256(f"{sandbox.sandbox_id}:{boot_id}".encode()).hexdigest()[:24]
        )
        commands.mount_id = mount_id
        files = E2BFiles(commands)
        await files.stat("/")
        outputs = E2BOutputs(commands)
        processes = E2BProcesses(commands, outputs, self.environment_id, self._process_owner)
        if self._processes is not None:
            await self._processes.disconnect()
        if self._commands is not None:
            self._commands.closed = True
        self._commands = commands
        self._processes = processes
        self._operations = EnvironmentOperations(
            files=files,
            shell=processes if not self._configuration.read_only else None,
            processes=processes if not self._configuration.read_only else None,
            outputs=outputs if not self._configuration.read_only else None,
            ports=E2BPorts(commands),
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


def decode_state(
    configuration: E2BProviderConfiguration, state: EnvironmentState | None
) -> E2BProviderStateData | None:
    if state is None:
        return None
    if state.provider_key != PROVIDER_KEY or state.state_version != "1":
        raise provider_error("provider_state_invalid", Category.INVALID)
    try:
        result = E2BProviderStateData.model_validate(state.state)
    except ValueError:
        raise provider_error("provider_state_invalid", Category.INVALID) from None
    if result.configuration_fingerprint != configuration.fingerprint:
        raise provider_error("provider_target_conflict", Category.CONFLICT)
    return result


def descriptor(
    configuration: E2BProviderConfiguration, generation: str = "unprepared", identity: str | None = None
) -> EnvironmentDescriptor:
    actions = {action for action in EnvironmentAction if not action.value.startswith("environment.state.")}
    if configuration.read_only:
        actions = {
            EnvironmentAction.FILE_STAT,
            EnvironmentAction.FILE_READ_TEXT,
            EnvironmentAction.FILE_READ_BYTES,
            EnvironmentAction.FILE_LIST,
            EnvironmentAction.FILE_QUERY,
            EnvironmentAction.FILE_SEARCH_TEXT,
            EnvironmentAction.FILE_COPY_SOURCE,
            EnvironmentAction.PORT_INSPECT,
            EnvironmentAction.PORT_WAIT,
        }
    return EnvironmentDescriptor(
        generation=generation,
        backing_identity=identity,
        operation_families=frozenset(ENVIRONMENT_ACTION_DISPATCH[action].family for action in actions),
        permissions=EnvironmentPermissionSet(operations=frozenset(actions)),
        mounts=(EnvironmentMountDescriptor(name="root", path="/", read_only=configuration.read_only),),
        limits={
            "max_value_bytes": configuration.max_file_bytes,
            "max_output_bytes": configuration.max_output_bytes,
            "max_processes": configuration.max_processes,
            "max_wall_time_seconds": configuration.max_wall_time_seconds,
        },
    )
