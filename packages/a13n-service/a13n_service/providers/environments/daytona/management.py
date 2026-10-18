"""Daytona management implementation."""

import asyncio
import shlex
from urllib.parse import urlsplit, urlunsplit

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.daytona.shared import (
    DaytonaEnvironmentConfiguration,
    DaytonaReference,
    Execution,
    Sandbox,
)
from a13n_harness.providers.environment.native.configuration import TargetState, TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.http import decode_response
from pydantic import BaseModel, Field

from a13n_service.providers.environment import desktop_access as access
from a13n_service.providers.environment.native import NativeManagement

from .configuration import DaytonaRecipe


class SignedPreview(BaseModel):
    url: str = Field(repr=False)


class DaytonaManagement(DaytonaReference, NativeManagement[DaytonaEnvironmentConfiguration, TargetState]):
    def __init__(
        self,
        configuration: DaytonaRecipe,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime | None,
        operation_id: str,
    ):
        if runtime is None:
            raise TypeError("Daytona management requires a runtime")
        super().__init__(configuration, environment_id, state, runtime)
        self.recipe = configuration
        del operation_id

    async def wait_for(self, desired: str) -> Sandbox:
        async with asyncio.timeout(self.config.request_timeout_seconds):
            while True:
                target = await self.lookup()
                if target is None:
                    raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
                if target.state == desired:
                    return target
                if target.state in {"error", "build_failed"}:
                    raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)
                await asyncio.sleep(0.5)

    async def create(self) -> None:
        target = await self.lookup()
        if target is None:
            if self.target is not None:
                raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
            raw = await self.transport().request(
                "POST",
                "sandbox",
                body={
                    "name": self.name,
                    "snapshot": self.config.snapshot,
                    "target": self.backend.target,
                    "labels": self.labels,
                    "public": False,
                    "autoStopInterval": 0,
                    "autoPauseInterval": 0,
                    "autoDeleteInterval": -1,
                    "ttlMinutes": 0,
                },
            )
            target = decode_response(Sandbox, raw, self.provider_key, mutation=True)
            self.accept(target)
        await self.start()

    async def start(self) -> None:
        target = await self.lookup()
        if target is None:
            raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
        if target.state in {"stopped", "archived", "paused"}:
            await self.transport().request("POST", self.path + "/start")
        target = await self.wait_for("started")
        if self.config.desktop:
            toolbox = await self.open_toolbox(target)
            await toolbox.request("POST", target.id + "/computeruse/start")
            # Validate desktop readiness without allocating or initializing during attachment.
            from a13n_harness.providers.environment.daytona.execution import DaytonaExecution

            runtime = NativeRuntime(configuration=self.backend, credential=TokenCredential(api_key=self.token))
            execution = DaytonaExecution(self.config, self.environment_id, self.state, runtime)
            try:
                await execution.open(execution_id="desktop-readiness")
            finally:
                await execution.close()
            if self.recipe.desktop_assistance:
                await self.desktop_access(access.Describe())

    async def desktop_access(self, request: access.Request) -> access.Reply:
        if not self.recipe.desktop_assistance or not self.config.desktop or self.target is None:
            raise failure(self.provider_key, "desktop_access_unavailable", Category.UNSUPPORTED)
        target = await self.lookup()
        if target is None or target.state != "started":
            raise failure(self.provider_key, "desktop_target_unavailable", Category.CONFLICT)
        toolbox = await self.open_toolbox(target)
        # The snapshot starts this endpoint. Access never installs, starts or repairs it.
        result = decode_response(
            Execution,
            await toolbox.request(
                "POST",
                target.id + "/process/execute",
                body={
                    "command": shlex.join(["sudo", "-n", *access.command(request)]),
                    "timeout": 25,
                },
            ),
            self.provider_key,
            mutation=True,
        )
        reply = access.decode(self.provider_key, request, result.result)
        if isinstance(reply, access.Description):
            preview = decode_response(
                SignedPreview,
                await self.transport().request(
                    "GET",
                    self.path + f"/ports/{access.PORT}/signed-preview-url",
                    params={"expiresInSeconds": "120"},
                ),
                self.provider_key,
                mutation=True,
            )
            url = urlsplit(preview.url)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.path not in {"", "/"}
            ):
                raise failure(self.provider_key, "provider_response_invalid", Category.UNAVAILABLE)
            # Signed host authorizes only this port; the supervisor token/password
            # still owns each viewer's input permission, lease and forced disconnect.
            return reply.model_copy(
                update={
                    "websocket_url": urlunsplit(("wss", url.netloc, "/websockify", "", "")),
                }
            )
        return reply

    async def stop(self) -> None:
        target = await self.lookup()
        if target is None or target.state in {"stopped", "archived", "paused"}:
            return
        if target.autoDeleteInterval is None or target.autoDeleteInterval >= 0:
            raise failure(self.provider_key, "provider_stop_retention_unsupported", Category.UNSUPPORTED)
        if target.state != "stopping":
            await self.transport().request("POST", self.path + "/stop")
        await self.wait_for("stopped")

    async def destroy(self) -> None:
        if await self.lookup() is not None:
            await self.transport().request("DELETE", self.path, missing=True)

        await self.confirm_deleted(self.lookup)
