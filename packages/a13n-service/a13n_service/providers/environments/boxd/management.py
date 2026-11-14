"""boxd management implementation."""

import asyncio

from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
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

_TRANSITIONAL = frozenset({"pending", "starting", "migrating"})


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

    async def wait_for(self, *desired: str) -> str:
        async with asyncio.timeout(self.config.request_timeout_seconds):
            while True:
                machine = await self.lookup()
                if machine is None:
                    raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
                if machine.status in desired:
                    return machine.status
                if machine.status == "failed":
                    raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)
                await asyncio.sleep(0.5)

    async def create(self) -> None:
        machine = await self.lookup()
        if machine is None:
            if self.target is not None:
                raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
            vcpu, memory = SIZES[self.config.size] if self.config.size else (None, None)
            try:
                # Agents run untrusted code: the machine never joins the organization's private network.
                with sdk_errors(mutation=True):
                    created = await self.client().machines.create(
                        self.name,
                        org=self.backend.organization,
                        isolated=True,
                        labels=self.labels,
                        # A snapshot restore replays the captured machine and takes no creation environment.
                        env=None if self.config.from_snapshot else self.config.environment or None,
                        vcpu=vcpu,
                        memory=memory,
                        from_snapshot=self.config.from_snapshot,
                        auto_suspend_timeout=self.config.auto_suspend_timeout_seconds,
                        region=self.backend.region,
                    )
            except EnvironmentProviderError as error:
                if error.code == "provider_target_conflict":
                    # The deterministic name exists: an earlier attempt allocated it. Adopt it only if it is ours.
                    machine = await self.get(self.name)
                    if machine is None:
                        raise
                    self.remember(machine)
                elif error.code == "provider_target_missing" and self.config.from_snapshot:
                    raise failure(
                        self.provider_key, "provider_spec_invalid", Category.INVALID, certainty=Certainty.KNOWN
                    ) from None
                else:
                    raise
            else:
                # The ID is this operation's own allocation, so it is recorded before any further read.
                # start() then reads the machine back and validates its ownership labels on that read.
                self.record(created.id)
        await self.start()

    async def apply_idle_policy(self, machine_id: str, hibernate_after: int) -> None:
        if hibernate_after != self.config.auto_hibernate_timeout_seconds:
            with sdk_errors(mutation=True):
                await self.client().machines.set_auto_hibernate_timeout(
                    machine_id, self.config.auto_hibernate_timeout_seconds
                )

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
        # Idle hibernation would leave a ready instance stopped; it is off unless the recipe asks for it.
        await self.apply_idle_policy(machine.id, machine.idle.hibernate_after)
        # Readiness covers the first command, not only the scheduler's running state.
        with sdk_errors(mutation=False):
            # One deadline bounds the whole readiness wait, including its first command.
            async with asyncio.timeout(self.config.request_timeout_seconds):
                await self.client().machines.wait_until_ready(machine.id, timeout=self.config.request_timeout_seconds)

    async def stop(self) -> None:
        machine = await self.lookup()
        if machine is None or machine.status in {"hibernated", "stopped"}:
            return
        if machine.status in _TRANSITIONAL:
            if await self.wait_for("running", "hibernated", "stopped") != "running":
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
