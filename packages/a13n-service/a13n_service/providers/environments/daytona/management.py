"""Daytona management implementation."""

import asyncio
import shlex
from urllib.parse import urlsplit, urlunsplit

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import provider_error
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.daytona.http import HttpReference, http_execution
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
        if self.config.snapshot == "auto":
            raise failure(self.provider_key, "desktop_snapshot_not_prepared", Category.INVALID)
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
                    "env": {
                        "A13N_ENVD_DEVICE_ID": self.owner,
                        "A13N_ENVD_TRANSPORT": "http",
                        "A13N_ENVD_HTTP_BIND": "0.0.0.0:8888",
                        "A13N_ENVD_HTTP_PLAINTEXT_SCOPE": "provider_private_link",
                        "A13N_ENVD_EXECUTION_UID": "1000",
                        "A13N_ENVD_EXECUTION_GID": "1000",
                        "A13N_ENVD_FULL_CONTROL": "true",
                        "A13N_ENVD_STATE_DIR": "/var/lib/a13n-envd",
                        "A13N_ENVD_RUNTIME_DIR": "/run/a13n-envd-state",
                        "A13N_DESKTOP": "true" if self.config.desktop else "false",
                        "A13N_DESKTOP_ACCESS": "true" if self.recipe.desktop_assistance else "false",
                    },
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
        await self._check_envd(target)
        if self.recipe.desktop_assistance:
            await self.desktop_access(access.Describe())

    async def _check_envd(self, target: Sandbox) -> None:
        # Snapshot boot owns startup on create and restart. Attachment only reads
        # credentials and checks EIP; it cannot repair a failed daemon.
        toolbox = await self.open_toolbox(target)
        probe = """import socket,time
end=time.monotonic()+30
while time.monotonic()<end:
 try:
  with socket.create_connection(('127.0.0.1',8888),timeout=1): break
 except OSError: time.sleep(.2)
else: raise RuntimeError('a13n-envd did not become ready')
"""
        result = decode_response(
            Execution,
            await toolbox.request(
                "POST",
                target.id + "/process/execute",
                body={
                    "command": shlex.join(["python3", "-I", "-c", probe]),
                    "timeout": 35,
                },
            ),
            self.provider_key,
        )
        if result.exitCode != 0:
            raise provider_error(
                self.provider_key,
                "envd_bootstrap_failed",
                Category.UNAVAILABLE,
                description="The Daytona snapshot must start compatible a13n-envd and desktop services at boot",
            )
        runtime = NativeRuntime(self.backend, TokenCredential(api_key=self.token))
        execution = http_execution(HttpReference(self.config, self.environment_id, self.state, runtime))
        try:
            await execution.open(execution_id="exec-readiness")
            await execution.check_ready(frozenset({"computer"}) if self.config.desktop else frozenset())
        finally:
            await execution.close()

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
