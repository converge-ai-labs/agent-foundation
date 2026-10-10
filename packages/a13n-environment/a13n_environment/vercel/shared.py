"""Vercel shared implementation."""

from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from ..errors import EnvironmentProviderErrorCategory as Category
from ..models import EnvironmentState
from ..native.configuration import CommandConfiguration, NamedTargetState, TokenCredential
from ..native.environment import NativeReference, NativeRuntime
from ..native.errors import failure
from ..native.http import NativeHTTP, decode_response


class VercelConnectionConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    team_id: str = Field(min_length=1, max_length=128, title="Team ID")
    project_id: str = Field(min_length=1, max_length=128, title="Project ID")


class VercelEnvironmentConfiguration(CommandConfiguration):
    model_config = ConfigDict(
        frozen=True, extra="forbid", json_schema_extra={"x-primary-fields": ["runtime", "timeout_seconds", "vcpus"]}
    )
    runtime: str = Field(default="python3.13", min_length=1, max_length=128)
    timeout_seconds: int = Field(default=3600, ge=300, le=18000)
    vcpus: int = Field(default=2, ge=2, le=8)
    root: str = "/vercel/sandbox"
    python: str = "/vercel/runtimes/python/bin/python3"


class Sandbox(BaseModel):
    name: str
    persistent: bool
    status: str
    createdAt: int
    tags: dict[str, str] = {}


class Session(BaseModel):
    id: str
    status: str
    timeout: int
    startedAt: int | None = None


class SandboxResponse(BaseModel):
    sandbox: Sandbox
    session: Session


class VercelReference(NativeReference[VercelEnvironmentConfiguration, NamedTargetState]):
    def __init__(
        self,
        config: VercelEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
    ):
        super().__init__("vercel", config, environment_id, state, state_model=NamedTargetState)
        assert isinstance(runtime.configuration, VercelConnectionConfiguration)
        assert isinstance(runtime.credential, TokenCredential)
        self.backend = runtime.configuration
        self.token = runtime.credential.api_key
        self.http: NativeHTTP | None = None
        self.session: Session | None = None

    def transport(self) -> NativeHTTP:
        if self.http is None:
            self.http = NativeHTTP(
                self.provider_key,
                "https://api.vercel.com",
                self.token.get_secret_value(),
                self.config.request_timeout_seconds + 10,
                params={"teamId": self.backend.team_id},
            )
        return self.http

    @property
    def path(self) -> str:
        return "/v2/sandboxes/" + quote(self.target.target_id if self.target else self.name, safe="")

    def accept(self, response: SandboxResponse) -> None:
        identity = f"{response.sandbox.name}:{response.sandbox.createdAt}"
        if self.target and self.target.backing_id != identity:
            raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)
        self.validate_labels(response.sandbox.tags)
        if not response.sandbox.persistent:
            raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)
        self.observe(
            NamedTargetState(
                backing_id=identity,
                target_id=response.sandbox.name,
                environment_id=self.owner,
                configuration_fingerprint=self.config.fingerprint,
            )
        )
        self.session = response.session

    async def lookup(self) -> SandboxResponse | None:
        raw = await self.transport().request(
            "GET", self.path, params={"projectId": self.backend.project_id, "resume": "false"}, missing=True
        )
        if raw is None:
            return None
        response = decode_response(SandboxResponse, raw, self.provider_key)
        self.accept(response)
        return response

    async def status(self) -> Literal["running", "stopped", "absent"]:
        response = await self.lookup()
        if response is None:
            return "absent"
        if response.session.status == "running":
            return "running"
        if response.session.status == "stopped":
            return "stopped"
        raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)

    async def close_transport(self) -> None:
        if self.http:
            await self.http.close()
