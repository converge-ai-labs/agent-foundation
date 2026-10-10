"""Runloop shared implementation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import EnvironmentProviderOutcomeCertainty as Certainty
from ..models import EnvironmentState
from ..native.configuration import CommandConfiguration, TargetState, TokenCredential
from ..native.environment import NativeReference, NativeRuntime
from ..native.errors import failure
from ..native.http import NativeHTTP, decode_response


class RunloopConnectionConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    organization: str = Field(min_length=1, max_length=128, description="Runloop organization owning the API key.")


class RunloopEnvironmentConfiguration(CommandConfiguration):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        json_schema_extra={"x-primary-fields": ["blueprint_id", "resource_size", "idle_timeout_seconds"]},
    )
    root: str = "/home/user"
    blueprint_id: str | None = Field(default=None, max_length=128)
    resource_size: Literal["X_SMALL", "SMALL", "MEDIUM", "LARGE", "X_LARGE"] = "SMALL"
    idle_timeout_seconds: int = Field(default=3600, ge=300, le=172800)


class IdlePolicy(BaseModel):
    idle_time_seconds: int = Field(gt=0)
    on_idle: Literal["shutdown", "suspend"]


class Lifecycle(BaseModel):
    after_idle: IdlePolicy | None = None


class LaunchParameters(BaseModel):
    after_idle: IdlePolicy | None = None
    lifecycle: Lifecycle | None = None


class Devbox(BaseModel):
    id: str
    status: str
    launch_parameters: LaunchParameters
    name: str | None = None
    metadata: dict[str, str] = {}


class Devboxes(BaseModel):
    devboxes: list[Devbox]
    has_more: bool = False


class Execution(BaseModel):
    exit_status: int
    stdout: str
    stderr: str


class RunloopReference(NativeReference[RunloopEnvironmentConfiguration, TargetState]):
    def __init__(
        self,
        config: RunloopEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
    ):
        super().__init__("runloop", config, environment_id, state, state_model=TargetState)
        assert isinstance(runtime.credential, TokenCredential)
        self.token = runtime.credential.api_key
        self.http: NativeHTTP | None = None

    def transport(self) -> NativeHTTP:
        if self.http is None:
            self.http = NativeHTTP(
                self.provider_key,
                "https://api.runloop.ai",
                self.token.get_secret_value(),
                self.config.request_timeout_seconds + 10,
            )
        return self.http

    async def lookup(self) -> Devbox | None:
        http = self.transport()
        target = None
        if self.target:
            raw = await http.request("GET", f"/v1/devboxes/{self.target.target_id}", missing=True)
            target = decode_response(Devbox, raw, self.provider_key) if raw is not None else None
        if self.target is None:
            # Pagination is essential: an interrupted create may be beyond the first page.
            target = None
            params = {"limit": "100"}
            while True:
                page = decode_response(
                    Devboxes, await http.request("GET", "/v1/devboxes", params=params), self.provider_key
                )
                for item in page.devboxes:
                    if item.metadata.get("a13n_environment") == self.owner and item.status != "shutdown":
                        if target is not None:
                            raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)
                        target = item
                if not page.has_more:
                    break
                if not page.devboxes or params.get("starting_after") == page.devboxes[-1].id:
                    raise failure(
                        self.provider_key,
                        "provider_response_invalid",
                        Category.UNKNOWN_OUTCOME,
                        certainty=Certainty.UNKNOWN,
                    )
                params["starting_after"] = page.devboxes[-1].id
        if target:
            self.remember_devbox(target)
            if target.status == "shutdown":
                return None
        return target

    def remember_devbox(self, target: Devbox) -> None:
        self.validate_labels(target.metadata)
        self.observe(
            TargetState(
                target_id=target.id, environment_id=self.owner, configuration_fingerprint=self.config.fingerprint
            )
        )

    async def status(self) -> Literal["running", "stopped", "absent"]:
        target = await self.lookup()
        if target is None:
            return "absent"
        if target.status == "suspended":
            return "stopped"
        if target.status == "running":
            return "running"
        raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)

    async def close_transport(self) -> None:
        if self.http:
            await self.http.close()
