"""Sprites management implementation."""

import base64
import shlex

from a13n_envd_client.eip.v1.models import ComputerDescribeParams, EIPCallContext
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.http import decode_response
from a13n_harness.providers.environment.sprites.commands import command
from a13n_harness.providers.environment.sprites.http import HttpReference, http_session
from a13n_harness.providers.environment.sprites.shared import (
    SpriteInfo,
    SpritesEnvironmentConfiguration,
    SpritesReference,
    SpritesTargetState,
)

from a13n_service.providers.environment import desktop_access as access
from a13n_service.providers.environment.native import NativeManagement

from .configuration import SpritesRecipe
from .desktop_install import install, revision


class SpritesManagement(SpritesReference, NativeManagement[SpritesEnvironmentConfiguration, SpritesTargetState]):
    def __init__(
        self,
        configuration: SpritesRecipe,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime | None,
        operation_id: str,
    ):
        if runtime is None:
            raise TypeError("Sprites requires a runtime")
        super().__init__(configuration, environment_id, state, runtime)
        self.recipe = configuration
        self.operation_id = operation_id

    async def create(self) -> None:
        info = await self.lookup()
        if info is None:
            if self.target is not None:
                raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
            raw = await self.transport().request(
                "POST",
                "/v1/sprites",
                body={
                    "name": self.name,
                    "labels": [f"{key}={value}" for key, value in self.labels.items()],
                    "config": {"region": self.config.region} if self.config.region else {},
                    "url_settings": {"auth": "sprite"},
                },
            )
            info = decode_response(SpriteInfo, raw, self.provider_key, mutation=True)
            if info.name != self.name or not all(f"{key}={value}" in info.labels for key, value in self.labels.items()):
                raise failure("sprites", "provider_target_conflict", Category.CONFLICT, certainty=Certainty.UNKNOWN)
            self.observe(
                SpritesTargetState(
                    desktop_phase="installing" if self.config.desktop else "ready",
                    install_revision=revision() if self.config.desktop else None,
                    backing_id=info.id,
                    target_id=info.name,
                    environment_id=self.owner,
                    configuration_fingerprint=self.config.fingerprint,
                )
            )
        self._info = info
        if self.config.desktop:
            assert self.target is not None
            if self.target.install_revision is None:
                # A lost allocation acknowledgement is reconciled by ownership labels.
                self.observe(
                    self.target.model_copy(update={"desktop_phase": "installing", "install_revision": revision()})
                )
            if self.target.install_revision != revision():
                raise failure("sprites", "desktop_install_revision_changed", Category.CONFLICT)
            if self.target.desktop_phase in {"installing", "failed"}:
                result = await install(self, self.operation_id)
                if result == "running":
                    self._phase("installing")
                    self._pending()
                if result != "ready":
                    self._phase("failed")
                    raise failure("sprites", "desktop_install_failed", Category.INVALID, certainty=Certainty.KNOWN)
                self._phase("starting")
                self._pending()
            await self._ready()
            self._phase("ready")

    def _phase(self, phase: str) -> None:
        assert self.target is not None
        self.observe(SpritesTargetState.model_validate({**self.target.model_dump(), "desktop_phase": phase}))

    @staticmethod
    def _pending() -> None:
        raise failure("sprites", "sprites_desktop_pending", Category.UNAVAILABLE, certainty=Certainty.KNOWN)

    async def _ready(self) -> None:
        # This service alone owns the public route. Envd stays on the authenticated TCP proxy.
        script = "\n".join(
            [
                "#!/bin/sh",
                "set -eu",
                "export A13N_ENVD_DEVICE_ID=" + shlex.quote(self.owner),
                "export A13N_ENVD_TRANSPORT=http",
                "export A13N_ENVD_HTTP_BIND=127.0.0.1:8888",
                "export A13N_ENVD_HTTP_PLAINTEXT_SCOPE=provider_private_link",
                "export A13N_ENVD_EXECUTION_UID=1002",
                "export A13N_ENVD_EXECUTION_GID=1002",
                "export A13N_ENVD_FULL_CONTROL=true",
                "export A13N_ENVD_STATE_DIR=/var/lib/a13n-envd",
                "export A13N_ENVD_RUNTIME_DIR=/run/a13n-envd-state",
                "export A13N_DESKTOP_ACCESS=true",
                "exec 9>/var/lib/a13n-envd/start.lock",
                "flock -n 9",
                "rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 /run/a13n-desktop-access/control.sock",
                "cd /workspace",
                "exec a13n-sandbox-entrypoint a13n-sandbox-desktop",
                "",
            ]
        )
        encoded = base64.b64encode(script.encode()).decode()
        await command(
            self,
            [
                "sudo",
                "-n",
                "sh",
                "-c",
                "set -eu; if [ ! -e /usr/local/bin/a13n-sprites-start ]; then "
                + "printf %s "
                + shlex.quote(encoded)
                + " | base64 -d > /usr/local/bin/a13n-sprites-start; "
                + "chmod 0755 /usr/local/bin/a13n-sprites-start; fi",
            ],
            15,
        )
        service = await self.transport().request("GET", self.path + "/services/a13n-desktop", missing=True)
        if service is None:
            await command(
                self,
                [
                    "sprite-env",
                    "services",
                    "create",
                    "a13n-desktop",
                    "--cmd",
                    "/usr/bin/sudo",
                    "--args",
                    "-n,/usr/local/bin/a13n-sprites-start",
                    "--http-port",
                    "6081",
                    "--no-stream",
                ],
                30,
            )
        elif (
            not isinstance(service, dict)
            or service.get("cmd") != "/usr/bin/sudo"
            or service.get("args") != ["-n", "/usr/local/bin/a13n-sprites-start"]
            or service.get("http_port") != 6081
        ):
            raise failure("sprites", "desktop_service_conflict", Category.CONFLICT)
        probe = """import socket,time
for _ in range(150):
 try:
  with socket.create_connection(('127.0.0.1',8888),timeout=1): pass
  with socket.create_connection(('127.0.0.1',6081),timeout=1): pass
  break
 except OSError: time.sleep(.2)
else: raise RuntimeError('Desktop readiness timed out')
"""
        await command(self, ["python3", "-I", "-c", probe], 35)
        reference = HttpReference(
            self.config,
            self.environment_id,
            self.state,
            NativeRuntime(self.backend, TokenCredential(api_key=self.token)),
        )
        async with http_session(reference) as binding:
            description = await binding.session.client.computer_describe(
                ComputerDescribeParams(context=EIPCallContext(operation_id="op-desktop-readiness"))
            )
            if not description.observe_ready or not description.input_ready or not description.targets:
                raise failure("sprites", "desktop_unavailable", Category.UNAVAILABLE)
        # The guest authenticates every connection, even with a browser-reachable vendor route.
        if not self.recipe.desktop_assistance:
            return
        await self.desktop_access(access.Describe())
        await self.transport().request("PUT", self.path, body={"url_settings": {"auth": "public"}})

    async def desktop_access(self, request: access.Request) -> access.Reply:
        if not self.recipe.desktop_assistance or self.target is None:
            raise failure("sprites", "desktop_access_unavailable", Category.UNSUPPORTED)
        if await self.lookup() is None:
            raise failure("sprites", "provider_target_missing", Category.MISSING)
        raw = await command(self, ["sudo", "-n", *access.command(request)], 25, max_output=16384)
        reply = access.decode("sprites", request, raw)
        if isinstance(reply, access.Description):
            endpoint = self.desktop_endpoint()
            await self.backend._endpoint_policy.validate(endpoint)
            reply = reply.model_copy(
                update={"websocket_url": endpoint.replace("https://", "wss://", 1) + "/websockify"}
            )
        return reply

    async def start(self) -> None:
        self.require_target()
        if self.config.desktop and self.target and self.target.desktop_phase != "ready":
            raise failure("sprites", "desktop_not_ready", Category.CONFLICT)
        if await self.lookup() is None:
            raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
        if self.config.desktop:
            await self._ready()

    async def stop(self) -> None:
        raise failure(self.provider_key, "provider_stop_unsupported", Category.UNSUPPORTED)

    async def destroy(self) -> None:
        if await self.lookup() is not None:
            await self.transport().request("DELETE", self.path, missing=True)

        await self.confirm_deleted(self.lookup)
