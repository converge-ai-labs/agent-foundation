"""kosa8 shared implementation over the kosa8 Cloud REST API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.native.configuration import CommandConfiguration, TargetState, TokenCredential
from a13n_harness.providers.environment.native.environment import NativeReference, NativeRuntime
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.http import NativeHTTP, decode_response

PROVIDER_KEY = "kosa8"
# The public API endpoint is fixed so an account never names a host.
API_URL = "https://api.kosa8.com"
# A sandbox in any other state never runs a command again; deleted ones are absent.
_FAILED = frozenset({"error", "stopped", "host_removed", "host_lost"})


class Kosa8ConnectionConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    region: Literal["us", "eu", "us-ash1", "eu-hel1"] | None = Field(
        default=None, description="kosa8 Cloud region for new sandboxes. Omitted: the organization default."
    )


class Kosa8EnvironmentConfiguration(CommandConfiguration):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        json_schema_extra={"x-primary-fields": ["size", "image", "disk_gb"]},
    )
    # The kosa8 files API reaches /workspace only, so every guest file path lives under it.
    root: str = "/workspace"
    size: Literal["2x4", "4x8", "8x16", "16x32"] = Field(default="2x4", description="vCPU x GiB memory.")
    image: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description=(
            "Boot image; it needs python3. Omitted: kosa8's universal Ubuntu template of the size,"
            " or, with disk_gb or docker, kosa8's default image."
        ),
    )
    disk_gb: int | None = Field(default=None, ge=8, le=200, description="Data disk in GB. Omitted: 32.")
    docker: bool = Field(default=False, description="Let the sandbox run its own dockerd.")
    ttl_seconds: int | None = Field(
        default=None,
        ge=60,
        le=7_776_000,
        description="kosa8 deletes the sandbox this long after creation. Omitted: no expiry.",
    )


class Sandbox(BaseModel):
    id: str = Field(min_length=1, max_length=256)
    state: str
    name: str | None = None
    labels: dict[str, str] = {}


class SandboxResponse(BaseModel):
    sandbox: Sandbox


class Sandboxes(BaseModel):
    sandboxes: list[Sandbox]
    next_cursor: str | None = None


class Kosa8Reference(NativeReference[Kosa8EnvironmentConfiguration, TargetState]):
    def __init__(
        self,
        config: Kosa8EnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
    ):
        super().__init__(PROVIDER_KEY, config, environment_id, state, state_model=TargetState)
        assert isinstance(runtime.configuration, Kosa8ConnectionConfiguration)
        self.backend = runtime.configuration
        assert isinstance(runtime.credential, TokenCredential)
        self.token = runtime.credential.api_key
        self.http: NativeHTTP | None = None

    def transport(self) -> NativeHTTP:
        if self.http is None:
            self.http = NativeHTTP(
                self.provider_key, API_URL, self.token.get_secret_value(), self.config.request_timeout_seconds + 10
            )
        return self.http

    async def get(self, sandbox_id: str) -> Sandbox | None:
        raw = await self.transport().request("GET", f"/v1/sandboxes/{sandbox_id}", missing=True)
        return None if raw is None else decode_response(SandboxResponse, raw, self.provider_key).sandbox

    async def lookup(self) -> Sandbox | None:
        """Read the recorded sandbox, or find an interrupted allocation by its ownership label."""
        if self.target:
            sandbox = await self.get(self.target.target_id)
        else:
            params = {"label": f"a13n_environment={self.owner}", "limit": "100"}
            found: list[Sandbox] = []
            while True:
                raw = await self.transport().request("GET", "/v1/sandboxes", params=params)
                page = decode_response(Sandboxes, raw, self.provider_key)
                found.extend(page.sandboxes)
                if not page.next_cursor:
                    break
                if params.get("cursor") == page.next_cursor:
                    raise failure(
                        self.provider_key, "provider_response_invalid", Category.UNAVAILABLE, certainty=Certainty.KNOWN
                    )
                params["cursor"] = page.next_cursor
            live = [item for item in found if item.state != "deleted"]
            if len(live) > 1:
                raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)
            sandbox = live[0] if live else None
        if sandbox is None or sandbox.state == "deleted":
            return None
        self.remember(sandbox)
        return sandbox

    def remember(self, sandbox: Sandbox) -> None:
        """Record a sandbox whose ownership labels came from a read of that sandbox."""
        self.validate_labels(sandbox.labels)
        self.record(sandbox.id)

    def record(self, sandbox_id: str) -> None:
        self.observe(
            TargetState(
                target_id=sandbox_id, environment_id=self.owner, configuration_fingerprint=self.config.fingerprint
            )
        )

    def phase(self, sandbox: Sandbox) -> Literal["running", "stopped"]:
        if sandbox.state == "running":
            return "running"
        # A sleeping sandbox keeps its memory and disk; only management wakes it, as idle sleep is off.
        if sandbox.state == "sleeping":
            return "stopped"
        raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)

    def failed(self, sandbox: Sandbox) -> bool:
        return sandbox.state in _FAILED

    async def status(self) -> Literal["running", "stopped", "absent"]:
        sandbox = await self.lookup()
        return "absent" if sandbox is None else self.phase(sandbox)

    async def close_transport(self) -> None:
        if self.http is not None:
            http, self.http = self.http, None
            await http.close()
