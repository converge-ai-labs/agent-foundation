"""Daytona control API and native toolbox exec, without an SDK-owned HTTP stack."""

import asyncio
import math
import shlex
from collections.abc import Mapping
from typing import Literal
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field

from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import EnvironmentProviderOutcomeCertainty as Certainty
from ..models import EnvironmentState
from ..native.configuration import CommandConfiguration, TargetState, TokenCredential
from ..native.environment import NativeEnvironment
from ..native.errors import failure
from ..native.factory import NativeProvider, NativeRuntime
from ..native.http import NativeHTTP, decode_response


class DaytonaBackendConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    organization_id: str = Field(min_length=1, max_length=128, title="Organization ID")
    target: str = Field(default="us", min_length=1, max_length=64, title="Target region")


class DaytonaConfiguration(CommandConfiguration):
    model_config = ConfigDict(
        frozen=True, extra="forbid", json_schema_extra={"x-primary-fields": ["snapshot", "cpu", "memory"]}
    )
    root: str = "/home/daytona"
    snapshot: str | None = Field(
        default=None, max_length=256, description="Existing Daytona snapshot; empty uses the default Python sandbox."
    )
    cpu: int = Field(default=2, ge=1, le=32)
    memory: int = Field(default=4, ge=1, le=128, description="Memory in GiB")
    disk: int = Field(default=10, ge=1, le=1024, description="Disk in GiB")


class Sandbox(BaseModel):
    id: str
    name: str
    organizationId: str
    labels: dict[str, str]
    state: str
    toolboxProxyUrl: str | None = None
    autoDestroyAt: str | None = None
    autoDeleteInterval: int | None = None


class Proxy(BaseModel):
    url: str


class Execution(BaseModel):
    exitCode: int
    result: str


class DaytonaEnvironment(NativeEnvironment[DaytonaConfiguration, TargetState]):
    def __init__(
        self, config: DaytonaConfiguration, environment_id: str, state: EnvironmentState | None, runtime: NativeRuntime
    ):
        super().__init__("daytona", config, environment_id, state, managed=runtime.managed, state_model=TargetState)
        assert isinstance(runtime.configuration, DaytonaBackendConfiguration)
        assert isinstance(runtime.credential, TokenCredential)
        self.backend = runtime.configuration
        self.token = runtime.credential.api_key
        self.http: NativeHTTP | None = None
        self.toolbox: NativeHTTP | None = None

    def transport(self) -> NativeHTTP:
        if self.http is None:
            self.http = NativeHTTP(
                self.provider_key,
                "https://app.daytona.io/api/",
                self.token.get_secret_value(),
                self.config.request_timeout_seconds + 10,
            )
        return self.http

    @property
    def path(self) -> str:
        return "sandbox/" + quote(self.target.target_id if self.target else self.name, safe="")

    def accept(self, target: Sandbox) -> None:
        self.validate_labels(target.labels)
        if target.organizationId != self.backend.organization_id:
            raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)
        self.remember(
            TargetState(
                target_id=target.id, environment_id=self.owner, configuration_fingerprint=self.config.fingerprint
            )
        )

    async def inspect(self) -> Sandbox | None:
        raw = await self.transport().request("GET", self.path, missing=True)
        if raw is None and self.target and self.managed:
            raw = await self.transport().request("GET", "sandbox/" + self.name, missing=True)
        if raw is None:
            return None
        target = decode_response(Sandbox, raw, self.provider_key)
        self.accept(target)
        return None if target.state == "destroyed" else target

    async def wait_for(self, desired: str) -> Sandbox:
        async with asyncio.timeout(self.config.request_timeout_seconds):
            while True:
                target = await self.inspect()
                if target is None:
                    raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
                if target.state == desired:
                    return target
                if target.state in {"error", "build_failed"}:
                    raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)
                await asyncio.sleep(0.5)

    async def _prepare(
        self, *, thread_id: str, run_id: str, agent_instance_id: str, mount_id: str, host_refs: Mapping[str, str]
    ) -> None:
        target = await self.inspect()
        if target is None:
            if not self.managed:
                raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
            raw = await self.transport().request(
                "POST",
                "sandbox",
                body={
                    "name": self.name,
                    "snapshot": self.config.snapshot,
                    "target": self.backend.target,
                    "labels": self.labels,
                    "cpu": self.config.cpu,
                    "memory": self.config.memory,
                    "disk": self.config.disk,
                    "public": False,
                    "autoStopInterval": 0,
                    "autoPauseInterval": 0,
                    "autoDeleteInterval": -1,
                    "ttlMinutes": 0,
                },
            )
            target = decode_response(Sandbox, raw, self.provider_key, mutation=True)
            self.accept(target)
        if target.state in {"stopped", "archived", "paused"}:
            await self.transport().request("POST", self.path + "/start")
        target = await self.wait_for("started")
        # Organization policy may impose a hard TTL even when creation requests none.
        if target.autoDestroyAt is not None:
            raise failure(self.provider_key, "provider_expiry_unsupported", Category.UNSUPPORTED)
        proxy = target.toolboxProxyUrl
        if proxy is None:
            proxy = decode_response(
                Proxy, await self.transport().request("GET", self.path + "/toolbox-proxy-url"), self.provider_key
            ).url
        parsed = urlsplit(proxy)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise failure(
                self.provider_key, "provider_response_invalid", Category.UNKNOWN_OUTCOME, certainty=Certainty.UNKNOWN
            )
        if self.toolbox:
            await self.toolbox.close()
        self.toolbox = NativeHTTP(
            self.provider_key,
            proxy.rstrip("/") + "/",
            self.token.get_secret_value(),
            self.config.request_timeout_seconds + 10,
        )
        await self.open_operations(self.execute, target.id, mount_id)

    async def execute(self, argv: list[str], timeout: float) -> str:
        assert self.target and self.toolbox
        result = decode_response(
            Execution,
            await self.toolbox.request(
                "POST",
                f"{self.target.target_id}/process/execute",
                body={"command": shlex.join(argv), "timeout": math.ceil(timeout)},
            ),
            self.provider_key,
            mutation=True,
        )
        if result.exitCode != 0:
            raise failure(
                self.provider_key, "provider_command_failed", Category.PROVIDER_FAILURE, certainty=Certainty.KNOWN
            )
        return result.result

    async def reconcile(self) -> Literal["running", "stopped", "absent"]:
        target = await self.inspect()
        if target is None:
            return "absent"
        if target.state == "started":
            return "running"
        if target.state in {"stopped", "archived", "paused"}:
            return "stopped"
        raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)

    async def _stop(self) -> None:
        target = await self.inspect()
        if target is None or target.state in {"stopped", "archived", "paused"}:
            return
        if target.autoDeleteInterval is None or target.autoDeleteInterval >= 0:
            raise failure(self.provider_key, "provider_stop_retention_unsupported", Category.UNSUPPORTED)
        if target.state != "stopping":
            await self.transport().request("POST", self.path + "/stop")
        await self.wait_for("stopped")

    async def _destroy(self) -> None:
        if not self.managed:
            raise failure(self.provider_key, "provider_denied", Category.DENIED)
        if await self.inspect() is not None:
            await self.transport().request("DELETE", self.path, missing=True)

        await self.confirm_deleted(self.inspect)

    async def close_transport(self) -> None:
        if self.toolbox:
            await self.toolbox.close()
        if self.http:
            await self.http.close()


class DaytonaEnvironmentProvider(NativeProvider):
    provider_key = "daytona"
    title = "Daytona"
    recipe_model = DaytonaConfiguration
    provider_configuration_model = DaytonaBackendConfiguration
    credential_model = TokenCredential
    supports_stop = True

    def build(
        self,
        configuration: CommandConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
    ) -> DaytonaEnvironment:
        assert isinstance(configuration, DaytonaConfiguration)
        return DaytonaEnvironment(configuration, environment_id, state, runtime)
