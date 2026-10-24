"""Explicit Cloudflare lifecycle; execution always uses the shared EIP adapter."""

import asyncio
from datetime import datetime, timedelta

from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.cloudflare.provider import http_execution
from a13n_harness.providers.environment.cloudflare.shared import (
    CloudflareEnvironmentConfiguration,
    CloudflareReference,
    CloudflareState,
    Target,
)
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.http import decode_response

from a13n_service.providers.environment.native import NativeManagement

from .bootstrap import prepare


class CloudflareManagement(CloudflareReference, NativeManagement[CloudflareEnvironmentConfiguration, CloudflareState]):
    def __init__(
        self,
        configuration: CloudflareEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime | None,
        operation_id: str,
    ):
        if runtime is None:
            raise TypeError("Cloudflare management requires a runtime")
        super().__init__(configuration, environment_id, state, runtime)
        self.operation_id = operation_id

    async def call(self, action: str, *, deadline: datetime | None = None, operation_id: str | None = None) -> Target:
        self.require_target()
        assert self.target is not None
        reply = await (await self.transport()).request(
            "POST",
            f"control/{action}",
            headers={"X-A13N-Environment": self.owner, "X-A13N-Fingerprint": self.config.fingerprint},
            body={
                "operation_id": operation_id or self.operation_id,
                "instance": self.config.instance,
                "version": self.target.lifecycle_version,
                "deadline": deadline.isoformat() if deadline else None,
            },
        )
        target = decode_response(Target, reply, "cloudflare", mutation=True)
        if target.environment_id != self.owner or target.fingerprint != self.config.fingerprint:
            raise failure("cloudflare", "provider_target_conflict", Category.CONFLICT)
        self.observe(self.target.model_copy(update={"lifecycle_version": target.version}))
        return target

    async def create(self) -> None:
        if self.target is None:
            endpoint = await prepare(self)
            self.observe(
                CloudflareState(
                    target_id=self.name,
                    account_id=self.backend.account_id,
                    deployment=self.script,
                    endpoint=endpoint,
                    environment_id=self.owner,
                    configuration_fingerprint=self.config.fingerprint,
                )
            )
        target = await self.call("create")
        if target.status != "running":
            raise failure("cloudflare", "provider_target_unavailable", Category.UNAVAILABLE)
        await self.ready()

    async def ready(self) -> None:
        async with asyncio.timeout(self.config.request_timeout_seconds):
            while True:
                execution = http_execution(self)
                try:
                    await execution.open(execution_id="exec-readiness")
                    await execution.check_ready(frozenset({"files", "shell"}))
                    return
                except EnvironmentProviderError as error:
                    if error.category not in {Category.UNAVAILABLE, Category.TIMEOUT}:
                        raise
                finally:
                    await execution.close()
                await asyncio.sleep(1)

    async def start(self) -> None:
        await self.call("start")
        await self.ready()

    async def stop(self) -> None:
        if (await self.call("stop")).status != "stopped":
            raise failure("cloudflare", "provider_stop_unconfirmed", Category.UNAVAILABLE)

    async def destroy(self) -> None:
        if self.target is None:
            return
        if (await self.call("destroy")).status != "absent":
            raise failure("cloudflare", "provider_delete_unconfirmed", Category.UNAVAILABLE)

    @property
    def keepalive_horizon(self) -> timedelta:
        return timedelta(seconds=300)

    async def keepalive(self, *, deadline: datetime, operation_id: str) -> datetime | None:
        await self.call("keepalive", deadline=deadline, operation_id=operation_id)
        return deadline
