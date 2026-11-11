"""Vercel management implementation."""

import asyncio
import hashlib
import math
import shlex
from datetime import UTC, datetime

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.endpoint_policy import EndpointPolicyError
from a13n_harness.providers.environment.native.configuration import NamedTargetState, TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.http import decode_response
from a13n_harness.providers.environment.vercel.commands import command
from a13n_harness.providers.environment.vercel.http import HttpReference, http_execution
from a13n_harness.providers.environment.vercel.shared import (
    SandboxResponse,
    VercelEnvironmentConfiguration,
    VercelReference,
)

from a13n_service.providers.environment import desktop_access as access
from a13n_service.providers.environment import envd_assets, envd_install, envd_upgrade
from a13n_service.providers.environment.envd_readiness import wait_until_ready
from a13n_service.providers.environment.management import Pending
from a13n_service.providers.environment.native import NativeManagement
from a13n_service.providers.environment.recipes.vercel import VercelRecipe

from . import preparation


class VercelManagement(VercelReference, NativeManagement[VercelEnvironmentConfiguration, NamedTargetState]):
    def __init__(
        self,
        configuration: VercelRecipe,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime | None,
        operation_id: str,
    ):
        if runtime is None:
            raise TypeError("Vercel management requires a runtime")
        super().__init__(configuration, environment_id, state, runtime)
        self.recipe = configuration
        self.operation_id = operation_id

    @property
    def automatic(self) -> bool:
        return self.config.execution != "native" and self.config.snapshot in {None, "auto"}

    async def create(self) -> Pending | None:
        self.pinned_session = None
        recovered = self.target is None
        response = await self.lookup()
        created = False
        if response is None:
            if self.target is not None:
                raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
            raw = await self.transport().request(
                "POST",
                "/v3/sandboxes" if self.config.execution != "native" else "/v2/sandboxes",
                body={
                    "name": self.name,
                    "projectId": self.backend.project_id,
                    **(
                        {
                            **(
                                {"image": preparation.specification(desktop=self.config.desktop)["image"]}
                                if self.automatic
                                else {"source": {"type": "snapshot", "snapshotId": self.config.snapshot}}
                            ),
                            "ports": [
                                *([8888] if self.config.execution == "envd_http" else []),
                                *([access.PORT] if self.recipe.desktop_assistance else []),
                            ],
                        }
                        if self.config.execution != "native"
                        else {"runtime": self.config.runtime}
                    ),
                    "persistent": True,
                    **(
                        {"env": self._envd_environment()}
                        if self.config.execution != "native"
                        else ({"env": self.config.environment} if self.config.environment else {})
                    ),
                    "timeout": self.config.timeout_seconds * 1000,
                    "resources": {"vcpus": self.config.vcpus},
                    "snapshotExpiration": 0,
                    "tags": {
                        **self.labels,
                        **(
                            {preparation.TAG: preparation.revision(desktop=self.config.desktop)}
                            if self.automatic
                            else {}
                        ),
                    },
                },
            )
            response = decode_response(SandboxResponse, raw, self.provider_key, mutation=True)
            self.accept(response)
            created = True
        if self.automatic and (created or recovered):
            # Publish the final native identity before dispatching any guest installation.
            return Pending(self.state, settled=True)
        if created and self.config.execution != "native":
            return await self._ready(response)
        return await self.start()

    async def start(self) -> Pending | None:
        self.pinned_session = None
        response = await self.lookup()
        if response is None:
            raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
        resumed = response.session.status == "stopped"
        if resumed:
            raw = await self.transport().request(
                "GET",
                self.path,
                params={"projectId": self.backend.project_id, "resume": "true"},
            )
            response = decode_response(SandboxResponse, raw, self.provider_key, mutation=True)
            self.accept(response)
        async with asyncio.timeout(self.config.request_timeout_seconds):
            while response is not None and response.session.status in {"pending", "stopping", "snapshotting"}:
                await asyncio.sleep(0.5)
                response = await self.lookup()
        if response is None or response.session.status != "running":
            raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)

        if self.config.execution != "native":
            self.pinned_session = response.session.id
            if self.automatic and not await preparation.prepare(self, response, self.operation_id):
                return Pending(self.state, settled=True)
            return await self._ready(response)
        return None

    def _envd_environment(self) -> dict[str, str]:
        return self.bootstrap_environment(
            {
                **self.config.environment,
                "A13N_ENVD_DEVICE_ID": self.owner,
                "A13N_ENVD_TRANSPORT": "http",
                "A13N_ENVD_HTTP_BIND": "0.0.0.0:8888",
                "A13N_ENVD_HTTP_PLAINTEXT_SCOPE": "provider_private_link",
                "A13N_ENVD_EXECUTION_UID": "1001",
                "A13N_ENVD_EXECUTION_GID": "1001",
                "A13N_ENVD_FULL_CONTROL": "true",
                "A13N_ENVD_STATE_DIR": "/var/lib/a13n-envd",
                "A13N_ENVD_RUNTIME_DIR": "/run/a13n-envd-state",
                "A13N_DESKTOP_ACCESS": "true" if self.recipe.desktop_assistance else "false",
            }
        )

    async def _ready(self, response: SandboxResponse) -> Pending | None:
        if response.session.status != "running":
            raise failure("vercel", "provider_unavailable", Category.UNAVAILABLE)
        self.pinned_session = response.session.id
        # Management may restart a dead launcher. Read-only execution attachment never does.
        # The guest lock makes repeated/lost launches harmless, including within one native session.
        if self.config.execution != "native" and self.recipe.envd_management == "managed":
            settings = self._envd_environment()
            program = "a13n-sandbox-desktop" if self.config.desktop else "a13n-envd"
            script = """set -eu
umask 077
install -d -m 0700 /var/lib/a13n-envd
cd /workspace
nohup flock -n /var/lib/a13n-envd/start.lock sh -c '
 rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 /run/a13n-desktop-access/control.sock
 STARTUP
' >> /var/lib/a13n-envd/startup.log 2>&1 < /dev/null &
""".replace("STARTUP", envd_assets.startup(program))
            await command(
                self,
                response.session.id,
                ["sh", "-c", script, "sh", program],
                env=settings,
                sudo=True,
                timeout=15,
            )
        elif self.recipe.startup_command is not None:
            # Reconcile every management entry, including lost allocation/resume replies.
            # One frozen guest job per native session prevents replay of authored side effects.
            script = "exec 9>&-\nexec " + shlex.join(self.recipe.startup_command)
            revision = hashlib.sha256(script.encode()).hexdigest()
            session = hashlib.sha256(response.session.id.encode()).hexdigest()
            status = (
                await command(
                    self,
                    response.session.id,
                    [
                        "sh",
                        "-c",
                        envd_install.dispatch(
                            script,
                            revision,
                            self.operation_id,
                            root=f"/var/lib/a13n-startup/{session}",
                            timeout_seconds=math.ceil(self.config.request_timeout_seconds),
                        ),
                    ],
                    env=self._envd_environment(),
                    sudo=True,
                    timeout=15,
                    max_output=1024,
                )
            ).strip()
            if status == "running":
                return Pending(self.state, settled=True)
            if status != "ready":
                raise failure(self.provider_key, "envd_startup_failed", Category.INVALID, certainty=Certainty.KNOWN)
        if self.defer_envd_readiness or self.config.execution == "envd_ws":
            return
        runtime = NativeRuntime(self.backend, TokenCredential(api_key=self.token))

        def execution():
            reference = HttpReference(self.config, self.environment_id, self.state, runtime)
            reference.pinned_session = response.session.id
            return http_execution(reference)

        await wait_until_ready(
            execution,
            provider_key=self.provider_key,
            desktop=self.config.desktop,
            timeout=self.config.request_timeout_seconds,
        )
        if self.recipe.desktop_assistance:
            await self.desktop_access(access.Describe())

    async def update_envd(self, version: str, operation_id: str) -> Pending | None:
        session_id = self.pinned_session or await self.running_session()

        async def execute(argv: list[str]) -> str:
            return await command(self, session_id, argv, sudo=True, timeout=15)

        if not await envd_upgrade.upgrade(
            execute, provider_key=self.provider_key, version=version, operation_id=operation_id
        ):
            return Pending(self.state, settled=True)
        response = await self.lookup()
        if response is None or response.session.id != session_id:
            raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)
        return await self._ready(response)

    async def desktop_access(self, request: access.Request) -> access.Reply:
        if not self.recipe.desktop_assistance or self.target is None:
            raise failure("vercel", "desktop_access_unavailable", Category.UNSUPPORTED)
        # Backend scope is one control-plane request. Subsequent commands use
        # this exact session; the guest still validates every generation/epoch.
        session_id = self.pinned_session or await self.running_session()
        raw = await command(self, session_id, access.command(request), sudo=True, timeout=25, max_output=16384)
        reply = access.decode("vercel", request, raw)
        if isinstance(reply, access.Description):
            endpoint = self.endpoint(access.PORT)
            try:
                await self.backend._endpoint_policy.validate(endpoint)
            except EndpointPolicyError:
                raise failure("vercel", "desktop_endpoint_denied", Category.INVALID) from None
            reply = reply.model_copy(
                update={"websocket_url": endpoint.replace("https://", "wss://", 1) + "/websockify"}
            )
        return reply

    async def stop(self) -> None:
        self.pinned_session = None
        response = await self.lookup()
        if response is None or response.session.status == "stopped":
            return
        # Vercel refuses a POST without a JSON body as an unsupported media type.
        await self.transport().request("POST", f"/v2/sandboxes/sessions/{response.session.id}/stop", body={})
        async with asyncio.timeout(self.config.request_timeout_seconds):
            while True:
                response = await self.lookup()
                if response is None or response.session.status == "stopped":
                    return
                if response.session.status in {"failed", "aborted"}:
                    raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)
                await asyncio.sleep(0.5)

    async def keepalive(self, *, deadline: datetime, operation_id: str) -> datetime:
        response = await self.lookup()
        if response is None or response.session.status != "running" or response.session.startedAt is None:
            raise failure(self.provider_key, "provider_target_stopped", Category.CONFLICT)
        if deadline.tzinfo is None or not operation_id:
            raise failure(self.provider_key, "provider_keepalive_invalid", Category.INVALID)
        session = response.session
        assert session.startedAt is not None
        expires_ms = session.startedAt + session.timeout
        needed = math.ceil(deadline.timestamp() * 1000) - expires_ms
        if needed > 0:
            if session.timeout + needed > self.config.timeout_seconds * 1000:
                raise failure(self.provider_key, "provider_keepalive_limit", Category.UNSUPPORTED)
            # A relative extension is never retried here after an uncertain response.
            await self.transport().request(
                "POST", f"/v2/sandboxes/sessions/{session.id}/extend-timeout", body={"duration": needed}
            )
            response = await self.lookup()
            if response is None or response.session.startedAt is None:
                raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)
            expires_ms = response.session.startedAt + response.session.timeout
        expiry = datetime.fromtimestamp(expires_ms / 1000, UTC)
        if expiry < deadline:
            raise failure(self.provider_key, "provider_keepalive_unsatisfied", Category.UNAVAILABLE)
        return expiry

    async def destroy(self) -> None:
        self.pinned_session = None
        if await self.lookup() is not None:
            await self.transport().request(
                "DELETE",
                self.path,
                params={"projectId": self.backend.project_id, "deleteOrphanSnapshots": "true"},
                missing=True,
            )

        await self.confirm_deleted(self.lookup)
