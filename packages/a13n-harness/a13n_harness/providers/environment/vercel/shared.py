"""Vercel shared implementation."""

import hashlib
from typing import Literal, Self
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.environment.native.configuration import (
    CommandConfiguration,
    NamedTargetState,
    TokenCredential,
)
from a13n_harness.providers.environment.native.environment import NativeReference, NativeRuntime
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.http import NativeHTTP, decode_response


class VercelConnectionConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    _endpoint_policy: EndpointPolicy = PrivateAttr(default_factory=EndpointPolicy)
    team_id: str = Field(min_length=1, max_length=128, title="Team ID")
    project_id: str = Field(min_length=1, max_length=128, title="Project ID")


class VercelEnvironmentConfiguration(CommandConfiguration):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        json_schema_extra={"x-primary-fields": ["execution", "snapshot", "desktop", "timeout_seconds", "vcpus"]},
    )
    execution: Literal["native", "envd_http"] = "native"
    snapshot: str | None = Field(default=None, min_length=1, max_length=256, pattern=r"^snap_[A-Za-z0-9_-]+$")
    desktop: bool = False

    @model_validator(mode="after")
    def execution_requirements(self) -> Self:
        if self.execution == "envd_http" and self.snapshot is None:
            raise ValueError("Envd execution requires an operator-built compatible snapshot")
        if self.execution == "native" and (self.desktop or self.snapshot is not None):
            raise ValueError("Desktop and snapshot require execution=envd_http")
        return self

    @property
    def fingerprint(self) -> str:
        # Existing native targets keep their exact pre-Envd recipe identity.
        excluded = {"desktop_assistance"}
        if not self.environment:
            excluded.add("environment")
        if self.execution == "native":
            excluded.update({"execution", "snapshot", "desktop"})
        return hashlib.sha256(self.model_dump_json(exclude=excluded).encode()).hexdigest()

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


class Route(BaseModel):
    url: str = Field(repr=False)
    port: int


class SandboxResponse(BaseModel):
    sandbox: Sandbox
    session: Session
    routes: list[Route] = []


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
        self.routes: list[Route] = []
        self.pinned_session: str | None = None

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
        if self.pinned_session is not None and response.session.id != self.pinned_session:
            raise failure(self.provider_key, "provider_execution_replaced", Category.CONFLICT)
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
        self.routes = response.routes

    async def lookup(self) -> SandboxResponse | None:
        raw = await self.transport().request(
            "GET", self.path, params={"projectId": self.backend.project_id, "resume": "false"}, missing=True
        )
        if raw is None:
            return None
        response = decode_response(SandboxResponse, raw, self.provider_key)
        self.accept(response)
        return response

    async def running_session(self) -> str:
        self.require_target()
        response = await self.lookup()
        if response is None:
            raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
        if response.session.status != "running":
            raise failure(self.provider_key, "provider_target_stopped", Category.CONFLICT)
        self.pinned_session = response.session.id
        return response.session.id

    def endpoint(self, port: int) -> str:
        routes = [route for route in self.routes if route.port == port]
        if len(routes) != 1:
            raise failure(self.provider_key, "provider_route_unavailable", Category.UNAVAILABLE)
        try:
            url = urlsplit(routes[0].url)
            route_port = url.port
        except ValueError:
            raise failure(self.provider_key, "provider_route_invalid", Category.UNAVAILABLE) from None
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
            or route_port not in {None, 443}
            or not url.hostname.endswith(".vercel.run")
        ):
            raise failure(self.provider_key, "provider_route_invalid", Category.UNAVAILABLE)
        return routes[0].url.rstrip("/")

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
            self.http = None
        self.pinned_session = None
