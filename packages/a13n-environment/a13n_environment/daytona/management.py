"""Daytona management implementation."""

import asyncio

from ..errors import EnvironmentProviderErrorCategory as Category
from ..models import EnvironmentState
from ..native.configuration import TargetState
from ..native.environment import NativeManagement, NativeRuntime
from ..native.errors import failure
from ..native.http import decode_response
from .shared import DaytonaEnvironmentConfiguration, DaytonaReference, Sandbox


class DaytonaManagement(DaytonaReference, NativeManagement[DaytonaEnvironmentConfiguration, TargetState]):
    def __init__(
        self,
        config: DaytonaEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
        operation_id: str,
    ):
        super().__init__(config, environment_id, state, runtime)
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
        await self.wait_for("started")

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
