"""Daytona shared implementation."""

from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from ..errors import EnvironmentProviderErrorCategory as Category
from ..models import EnvironmentState
from ..native.configuration import CommandConfiguration, TargetState, TokenCredential
from ..native.environment import NativeReference, NativeRuntime
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


class DaytonaReference(NativeReference[DaytonaEnvironmentConfiguration, TargetState]):
    def __init__(
        self,
        config: DaytonaEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
    ):
        super().__init__("daytona", config, environment_id, state, state_model=TargetState)
        assert isinstance(runtime.configuration, DaytonaConnectionConfiguration)
        assert isinstance(runtime.credential, TokenCredential)
        self.backend = runtime.configuration
        self.token = runtime.credential.api_key
        self.http: NativeHTTP | None = None

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
        self.observe(
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

    async def status(self) -> Literal["running", "stopped", "absent"]:
        target = await self.lookup()
        if target is None:
            return "absent"
        if target.state == "started":
            return "running"
        if target.state in {"stopped", "archived", "paused"}:
            return "stopped"
        raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)

    async def close_transport(self) -> None:
        if self.http:
            await self.http.close()
