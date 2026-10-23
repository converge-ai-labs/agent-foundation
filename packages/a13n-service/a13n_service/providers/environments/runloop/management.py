"""Runloop management implementation."""

import asyncio
from datetime import UTC, datetime, timedelta

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.native.configuration import TargetState
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.http import decode_response
from a13n_harness.providers.environment.runloop.shared import Devbox, RunloopEnvironmentConfiguration, RunloopReference

from a13n_service.providers.environment.native import NativeManagement


class RunloopManagement(RunloopReference, NativeManagement[RunloopEnvironmentConfiguration, TargetState]):
    def __init__(
        self,
        config: RunloopEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
        operation_id: str,
    ):
        super().__init__(config, environment_id, state, runtime)
        self.operation_id = operation_id

    async def wait_for(self, desired: str) -> Devbox:
        async with asyncio.timeout(self.config.request_timeout_seconds):
            while True:
                target = await self.lookup()
                if target is None:
                    raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
                if target.status == desired:
                    return target
                if target.status == "failure":
                    raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)
                await asyncio.sleep(0.5)

    async def create(self) -> None:
        target = await self.lookup()
        if target is None:
            if self.target is not None:
                raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
            raw = await self.transport().request(
                "POST",
                "/v1/devboxes",
                body={
                    "name": self.name,
                    "metadata": self.labels,
                    "blueprint_id": self.config.blueprint_id,
                    **({"environment_variables": self.config.environment} if self.config.environment else {}),
                    "launch_parameters": {
                        "resource_size_request": self.config.resource_size,
                        "after_idle": {"idle_time_seconds": self.config.idle_timeout_seconds, "on_idle": "suspend"},
                    },
                },
                headers={"x-request-id": self.operation_id},
            )
            target = decode_response(Devbox, raw, self.provider_key, mutation=True)
            self.remember_devbox(target)
        await self.start()

    async def start(self) -> None:
        target = await self.lookup()
        if target is None:
            raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
        if target.status == "suspended":
            await self.transport().request("POST", f"/v1/devboxes/{target.id}/resume")
        await self.wait_for("running")

    async def stop(self) -> None:
        target = await self.lookup()
        if target is None or target.status == "suspended":
            return
        if target.status != "suspending":
            await self.transport().request("POST", f"/v1/devboxes/{target.id}/suspend")
        await self.wait_for("suspended")

    async def keepalive(self, *, deadline: datetime, operation_id: str) -> datetime:
        target = await self.lookup()
        if target is None or target.status != "running":
            raise failure(self.provider_key, "provider_target_stopped", Category.CONFLICT)
        launch = target.launch_parameters
        policy = launch.lifecycle.after_idle if launch.lifecycle else launch.after_idle
        if policy is None:
            raise failure(self.provider_key, "provider_keepalive_limit", Category.UNSUPPORTED)
        lower_bound = datetime.now(UTC) + timedelta(seconds=policy.idle_time_seconds)
        if deadline.tzinfo is None or deadline > lower_bound or not operation_id:
            raise failure(self.provider_key, "provider_keepalive_limit", Category.UNSUPPORTED)
        await self.transport().request(
            "POST", f"/v1/devboxes/{target.id}/keep_alive", headers={"x-request-id": operation_id}
        )
        return lower_bound

    async def destroy(self) -> None:
        target = await self.lookup()
        if target:
            await self.transport().request("POST", f"/v1/devboxes/{target.id}/shutdown")

        await self.confirm_deleted(self.lookup)
