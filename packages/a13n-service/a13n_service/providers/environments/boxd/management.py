"""boxd management implementation."""

import asyncio

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.boxd.shared import (
    SIZES,
    BoxdEnvironmentConfiguration,
    BoxdReference,
    sdk_errors,
)
from a13n_harness.providers.environment.native.configuration import TargetState
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.native.errors import failure

from a13n_service.providers.environment.native import NativeManagement


class BoxdManagement(BoxdReference, NativeManagement[BoxdEnvironmentConfiguration, TargetState]):
    def __init__(
        self,
        config: BoxdEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
        operation_id: str,
    ):
        super().__init__(config, environment_id, state, runtime)
        self.operation_id = operation_id

    async def wait_for(self, desired: str) -> None:
        async with asyncio.timeout(self.config.request_timeout_seconds):
            while True:
                machine = await self.lookup()
                if machine is None:
                    raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
                if machine.status == desired:
                    return
                if machine.status == "failed":
                    raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)
                await asyncio.sleep(0.5)

    async def create(self) -> None:
        machine = await self.lookup()
        if machine is None:
            if self.target is not None:
                raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
            vcpu, memory = SIZES[self.config.size]
            # Agents run untrusted code: the machine never joins the organization's private network.
            with sdk_errors(mutation=True):
                machine = await self.client().machines.create(
                    self.name,
                    org=self.backend.organization,
                    isolated=True,
                    labels=self.labels,
                    vcpu=vcpu,
                    memory=memory,
                    from_snapshot=self.config.from_snapshot,
                    auto_suspend_timeout=self.config.auto_suspend_timeout_seconds,
                    region=self.backend.region,
                )
            self.remember(machine)
        await self.start()

    async def start(self) -> None:
        machine = await self.lookup()
        if machine is None:
            raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
        machines = self.client().machines
        with sdk_errors(mutation=True):
            if machine.status == "hibernated":
                await machines.wake(machine.id)
            elif machine.status == "stopped":
                await machines.start(machine.id)
            elif machine.status == "suspended":
                await machines.resume(machine.id)
        await self.wait_for("running")
        # Readiness covers the first command, not only the scheduler's running state.
        with sdk_errors(mutation=False):
            await machines.wait_until_ready(machine.id, timeout=self.config.request_timeout_seconds)

    async def stop(self) -> None:
        machine = await self.lookup()
        if machine is None or machine.status in {"hibernated", "stopped"}:
            return
        # Hibernation keeps memory and disk; the machine resumes with its processes on start.
        with sdk_errors(mutation=True):
            await self.client().machines.hibernate(machine.id)
        await self.wait_for("hibernated")

    async def destroy(self) -> None:
        machine = await self.lookup()
        if machine is not None:
            with sdk_errors(mutation=True):
                await self.client().machines.delete(machine.id)

        await self.confirm_deleted(self.lookup)
