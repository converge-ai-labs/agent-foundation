"""Daytona control API and native toolbox exec, without an SDK-owned HTTP stack."""

import asyncio
import math
import shlex
from typing import Literal
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field

from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import EnvironmentProviderOutcomeCertainty as Certainty
from ..models import EnvironmentState
from ..native.configuration import CommandConfiguration, TargetState, TokenCredential
from ..native.environment import NativeRuntime, NativeTarget, native_definition
from ..native.errors import failure
from ..native.http import NativeHTTP, decode_response


class DaytonaConnectionConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    organization_id: str = Field(min_length=1, max_length=128, title="Organization ID")
    target: str = Field(default="us", min_length=1, max_length=64, title="Target region")


class DaytonaEnvironmentConfiguration(CommandConfiguration):
    model_config = ConfigDict(frozen=True, extra="forbid", json_schema_extra={"x-primary-fields": ["snapshot"]})
    root: str = "/home/daytona"
    # Daytona sizes a sandbox by its snapshot and refuses resources requested alongside one.
    snapshot: str | None = Field(
        default=None,
        max_length=256,
        description="Existing Daytona snapshot, which sets the sandbox's resources; empty uses the default Python sandbox.",
    )


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


class DaytonaTarget(NativeTarget[DaytonaEnvironmentConfiguration, TargetState]):
    def __init__(
        self,
        config: DaytonaEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
        operation_id: str,
    ):
        del operation_id
        super().__init__("daytona", config, environment_id, state, state_model=TargetState)
        assert isinstance(runtime.configuration, DaytonaConnectionConfiguration)
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

    async def lookup(self) -> Sandbox | None:
        raw = await self.transport().request("GET", self.path, missing=True)
        if raw is None:
            return None
        target = decode_response(Sandbox, raw, self.provider_key)
        self.accept(target)
        return None if target.state == "destroyed" else target

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

    async def open(self, *, execution_id: str) -> None:
        self.require_target()
        target = await self.lookup()
        if target is None:
            raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
        if target.state != "started":
            raise failure(self.provider_key, "provider_target_stopped", Category.CONFLICT)
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
        await self.open_operations(self.execute, target.id, execution_id)

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

    async def inspect(self) -> Literal["running", "stopped", "absent"]:
        target = await self.lookup()
        if target is None:
            return "absent"
        if target.state == "started":
            return "running"
        if target.state in {"stopped", "archived", "paused"}:
            return "stopped"
        raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)

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

    async def close_transport(self) -> None:
        if self.toolbox:
            await self.toolbox.close()
        if self.http:
            await self.http.close()


DAYTONA = native_definition(
    type="daytona",
    display_name="Daytona",
    configuration_model=DaytonaConnectionConfiguration,
    credential_model=TokenCredential,
    environment_model=DaytonaEnvironmentConfiguration,
    state_model=TargetState,
    environment_type=DaytonaTarget,
    supports_stop=True,
    requires_keepalive=False,
)
