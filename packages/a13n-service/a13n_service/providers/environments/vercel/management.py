"""Vercel management implementation."""

import asyncio
import json
import math
from datetime import UTC, datetime

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
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
from a13n_service.providers.environment.native import NativeManagement

from .configuration import VercelRecipe


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
        del operation_id

    async def create(self) -> None:
        self.pinned_session = None
        response = await self.lookup()
        created = False
        if response is None:
            if self.target is not None:
                raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
            raw = await self.transport().request(
                "POST",
                "/v3/sandboxes" if self.config.execution == "envd_http" else "/v2/sandboxes",
                body={
                    "name": self.name,
                    "projectId": self.backend.project_id,
                    **(
                        {
                            "source": {"type": "snapshot", "snapshotId": self.config.snapshot},
                            "ports": [8888, *([access.PORT] if self.recipe.desktop_assistance else [])],
                        }
                        if self.config.execution == "envd_http"
                        else {"runtime": self.config.runtime}
                    ),
                    "persistent": True,
                    "timeout": self.config.timeout_seconds * 1000,
                    "resources": {"vcpus": self.config.vcpus},
                    "snapshotExpiration": 0,
                    "tags": self.labels,
                },
            )
            response = decode_response(SandboxResponse, raw, self.provider_key, mutation=True)
            self.accept(response)
            created = True
        if created and self.config.execution == "envd_http":
            await self._ready(response, bootstrap=True)
        else:
            await self.start()

    async def start(self) -> None:
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

        if self.config.execution == "envd_http":
            await self._ready(response, bootstrap=resumed)

    async def _ready(self, response: SandboxResponse, *, bootstrap: bool) -> None:
        if response.session.status != "running":
            raise failure("vercel", "provider_unavailable", Category.UNAVAILABLE)
        self.pinned_session = response.session.id
        if bootstrap:
            # Only a new native session starts services. Failed/recovered creates
            # and attachment must never repair an existing desktop implicitly.
            settings = {
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
            program = "a13n-sandbox-desktop" if self.config.desktop else "a13n-envd"
            # A snapshot retains disk, including stale X11 socket paths, but not
            # processes. The per-session marker fences duplicate bootstrap attempts.
            script = """import json,os,subprocess,sys
from pathlib import Path
session,program,settings=sys.argv[1:]
os.umask(0o077)
root=Path('/var/lib/a13n-envd');root.mkdir(mode=0o700,exist_ok=True)
marker=root/('session-'+session)
with marker.open('x'): pass
for name in ['/tmp/.X99-lock','/tmp/.X11-unix/X99','/run/a13n-desktop-access/control.sock']:
 Path(name).unlink(missing_ok=True)
with (root/'startup.log').open('ab') as log:
 subprocess.Popen(['flock','-n',str(root/'start.lock'),'a13n-sandbox-entrypoint',program],
  env={**os.environ,**json.loads(settings)},cwd='/workspace',stdin=subprocess.DEVNULL,
  stdout=log,stderr=log,start_new_session=True)
"""
            await command(
                self,
                response.session.id,
                ["python3", "-I", "-c", script, response.session.id, program, json.dumps(settings)],
                sudo=True,
                timeout=15,
            )
        probe = """import socket,time
end=time.monotonic()+30
while time.monotonic()<end:
 try:
  with socket.create_connection(('127.0.0.1',8888),timeout=1): break
 except OSError: time.sleep(.2)
else: raise RuntimeError('Envd readiness timed out')
"""
        await command(self, response.session.id, ["python3", "-I", "-c", probe], timeout=35)
        runtime = NativeRuntime(self.backend, TokenCredential(api_key=self.token))
        reference = HttpReference(self.config, self.environment_id, self.state, runtime)
        reference.pinned_session = response.session.id
        execution = http_execution(reference)
        try:
            await execution.open(execution_id="exec-readiness")
            await execution.check_ready(frozenset({"computer"}) if self.config.desktop else frozenset())
            if self.config.desktop:
                computer = execution.operations.computer
                if computer is None:
                    raise failure("vercel", "desktop_unavailable", Category.UNAVAILABLE)
                description = await computer.describe()
                if not description.observe_ready or not description.input_ready or not description.targets:
                    raise failure("vercel", "desktop_unavailable", Category.UNAVAILABLE)
        finally:
            await execution.close()
        if self.recipe.desktop_assistance:
            await self.desktop_access(access.Describe())

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
