"""E2B management implementation."""

from __future__ import annotations

import math
import shlex
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.e2b.configuration import (
    PROVIDER_KEY,
    E2BProviderStateData,
)
from a13n_harness.providers.environment.e2b.connection import close_sandbox, open_sandbox
from a13n_harness.providers.environment.e2b.desktop import Desktop, create_desktop
from a13n_harness.providers.environment.e2b.errors import provider_error, sdk_errors
from a13n_harness.providers.environment.e2b.shared import E2BProviderRuntime, E2BReference

from a13n_service.providers.environment._backend import ManagementBackend
from a13n_service.providers.environment.desktop_access import Description, Reply, Request, command, decode

if TYPE_CHECKING:
    from e2b import AsyncSandbox
    from e2b.sandbox.sandbox_api import SandboxInfo


from .configuration import E2BRecipe


class E2BManagement(E2BReference, ManagementBackend):
    _configuration: E2BRecipe

    def __init__(
        self,
        configuration: E2BRecipe,
        *,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: E2BProviderRuntime,
        operation_id: str,
    ):
        super().__init__(configuration, environment_id=environment_id, state=state, runtime=runtime)
        self._operation_id = operation_id

    def _remember(self, sandbox_id: str) -> None:
        self._state = E2BProviderStateData(
            sandbox_id=sandbox_id,
            environment_id=self._native_id,
            configuration_fingerprint=self._configuration.fingerprint,
        )
        self._cache_state(
            EnvironmentState(provider_key=PROVIDER_KEY, state_version="1", state=self._state.model_dump(mode="json"))
        )

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
                    query=SandboxQuery(metadata={"a13n_environment": self._native_id}),
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

    def validate_management(self) -> None:
        if self._state is not None and self._state.environment_id != self.environment_id:
            raise provider_error("provider_target_conflict", Category.CONFLICT)

    async def create(self) -> None:
        from e2b import AsyncSandbox

        target = await self._inspect()
        if target is not None:
            if target.state.value != "running" or self._configuration.desktop:
                await self.start()
            return
        if self._state is not None:
            raise provider_error("provider_target_missing", Category.MISSING)
        config = self._configuration
        metadata = {"a13n_environment": self._native_id, "a13n_configuration": config.fingerprint}
        if self._operation_id:
            metadata["a13n_operation"] = self._operation_id
        if config.desktop:
            await create_desktop(
                config,
                timeout=config.timeout_seconds,
                metadata=metadata,
                options=self._options(),
                allocated=self._remember,
            )
            return
        with sdk_errors(mutation=True):
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
            await close_sandbox(sandbox)

    async def start(self) -> None:
        from e2b import AsyncSandbox

        target = await self._inspect()
        if target is None:
            raise provider_error("provider_target_missing", Category.MISSING)
        if target.state.value == "running":
            if self._configuration.desktop:
                sandbox = await open_sandbox(target.sandbox_id, self._options(), self._validate_target)
                try:
                    await self._check_desktop(sandbox)
                finally:
                    await close_sandbox(sandbox)
            return
        with sdk_errors(mutation=True):
            sandbox = await AsyncSandbox.connect(
                target.sandbox_id, timeout=self._configuration.timeout_seconds, **self._options()
            )
            try:
                self._validate_target(await sandbox.get_info())
                if self._configuration.desktop:
                    await self._check_desktop(sandbox)
            finally:
                await close_sandbox(sandbox)

    async def _check_desktop(self, sandbox: AsyncSandbox) -> None:
        desktop = Desktop(sandbox, self._configuration)
        try:
            await desktop.describe()
        finally:
            await desktop.close()

    async def desktop_access(self, request: Request) -> Reply:
        if not self._configuration.desktop or self._state is None:
            raise self._unsupported()
        target = await self._inspect()
        if target is None or target.state.value != "running":
            raise provider_error("desktop_target_unavailable", Category.CONFLICT)
        sandbox = await open_sandbox(target.sandbox_id, self._options(), self._validate_target)
        try:
            # A compatible template owns this endpoint. Never start/repair a desktop while granting access.
            result = await sandbox.commands.run(
                shlex.join(command(request)),
                user="root",
                timeout=25,
                request_timeout=30,
            )
            reply = decode(PROVIDER_KEY, request, result.stdout)
            if isinstance(reply, Description) and self._configuration.desktop_assistance:
                return reply.model_copy(
                    update={"websocket_url": "wss://" + sandbox.get_host(reply.port) + "/websockify"}
                )
            return reply
        finally:
            await close_sandbox(sandbox)

    async def inspect(self) -> Literal["running", "stopped", "absent"]:
        target = await self._inspect()
        if target is None:
            return "absent"
        return "stopped" if target.state.value == "paused" else "running"

    async def stop(self) -> None:
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

    async def destroy(self) -> None:
        from e2b import AsyncSandbox

        target = await self._inspect()
        if target is not None:
            with sdk_errors(mutation=True):
                await AsyncSandbox.kill(target.sandbox_id, **self._options())
        self._state = None
