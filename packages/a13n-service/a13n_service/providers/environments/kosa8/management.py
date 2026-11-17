"""kosa8 management implementation."""

import asyncio

from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.kosa8.shared import (
    Kosa8EnvironmentConfiguration,
    Kosa8Reference,
    Sandbox,
    SandboxResponse,
)
from a13n_harness.providers.environment.native.configuration import TargetState
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.http import decode_response

from a13n_service.providers.environment.native import NativeManagement

_POLL_SECONDS = 0.5


class Kosa8Management(Kosa8Reference, NativeManagement[Kosa8EnvironmentConfiguration, TargetState]):
    def __init__(
        self,
        config: Kosa8EnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
        operation_id: str,
    ):
        super().__init__(config, environment_id, state, runtime)
        self.operation_id = operation_id

    async def wait_for(self, *desired: str) -> Sandbox:
        async with asyncio.timeout(self.config.request_timeout_seconds):
            while True:
                sandbox = await self.lookup()
                if sandbox is None:
                    raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
                if sandbox.state in desired:
                    return sandbox
                if self.failed(sandbox):
                    raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)
                await asyncio.sleep(_POLL_SECONDS)

    async def create(self) -> None:
        sandbox = await self.lookup()
        if sandbox is None:
            if self.target is not None:
                raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
            config = self.config
            body: dict[str, object] = {
                "name": self.name,
                "labels": self.labels,
                # Every command runs in one long-lived container, so the whole filesystem lasts between commands.
                "persistent": True,
                # Idle sleep is off: a sleeping sandbox is a stopped Environment that only start() wakes.
                "sleep_after_s": 0,
            }
            if config.image is None and config.disk_gb is None and not config.docker:
                # kosa8's universal template of this size: Ubuntu with python3, which the guest helper needs,
                # restored from a snapshot. A bare size boots kosa8's minimal default image, without python3.
                body["template"] = f"universal-{config.size}"
            else:
                body["size"] = config.size
            if config.image is not None:
                body["image"] = config.image
            if config.disk_gb is not None:
                body["disk_gb"] = config.disk_gb
            if config.docker:
                body["docker"] = True
            if config.ttl_seconds is not None:
                body["ttl_s"] = config.ttl_seconds
            if self.backend.region is not None:
                body["region"] = self.backend.region
            # Recipe environment defaults reach every command through the guest helper, not the create call.
            raw = await self.transport().request("POST", "/v1/sandboxes", body=body)
            created = decode_response(SandboxResponse, raw, self.provider_key, mutation=True).sandbox
            # The ID is this operation's own allocation, so it is recorded before any further read.
            # start() then reads the sandbox back and validates its ownership labels on that read.
            self.record(created.id)
        await self.start()

    async def start(self) -> None:
        sandbox = await self.lookup()
        if sandbox is None:
            raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
        if sandbox.state == "sleeping":
            # Waking restores memory and running processes from the sleep snapshot.
            await self.transport().request("POST", f"/v1/sandboxes/{sandbox.id}/wake")
        await self.wait_for("running")
        await self.wait_until_ready(sandbox.id)

    async def wait_until_ready(self, sandbox_id: str) -> None:
        """Readiness covers the first command, not only the scheduler's running state."""
        async with asyncio.timeout(self.config.request_timeout_seconds):
            while True:
                try:
                    await self.transport().request(
                        "POST", f"/v1/sandboxes/{sandbox_id}/exec", body={"cmd": ["true"], "timeout_s": 30}
                    )
                    return
                except EnvironmentProviderError as error:
                    # 409 starting: the guest has not taken a command yet.
                    if error.code != "provider_target_conflict":
                        raise
                await asyncio.sleep(_POLL_SECONDS)

    async def stop(self) -> None:
        sandbox = await self.lookup()
        if sandbox is None or sandbox.state == "sleeping":
            return
        if sandbox.state == "creating":
            sandbox = await self.wait_for("running", "sleeping")
            if sandbox.state == "sleeping":
                return
        # Sleep keeps memory and disk; the sandbox resumes with its processes on start.
        await self.transport().request("POST", f"/v1/sandboxes/{sandbox.id}/sleep")
        await self.wait_for("sleeping")

    async def destroy(self) -> None:
        sandbox = await self.lookup()
        if sandbox is not None:
            await self.transport().request("DELETE", f"/v1/sandboxes/{sandbox.id}", missing=True)

        await self.confirm_deleted(self.lookup)
