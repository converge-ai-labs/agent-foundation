"""Sprites shared implementation."""

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


class SpritesConnectionConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    _endpoint_policy: EndpointPolicy = PrivateAttr(default_factory=EndpointPolicy)
    organization: str = Field(
        min_length=1, max_length=128, description="Fly.io organization owning the token and Sprites."
    )


class SpritesEnvironmentConfiguration(CommandConfiguration):
    model_config = ConfigDict(
        frozen=True, extra="forbid", json_schema_extra={"x-primary-fields": ["desktop", "region"]}
    )
    root: str = "/home/sprite"
    region: str | None = Field(default=None, max_length=64)

    execution: Literal["native", "envd_http"] = "native"
    desktop: bool = False

    @model_validator(mode="after")
    def execution_requirements(self) -> Self:
        if self.desktop != (self.execution == "envd_http"):
            raise ValueError("Sprites desktop requires execution=envd_http")
        return self

    @property
    def fingerprint(self) -> str:
        excluded = {"desktop_assistance"}
        if not self.environment:
            excluded.add("environment")
        if self.execution == "native":
            excluded.update({"execution", "desktop"})
        return hashlib.sha256(self.model_dump_json(exclude=excluded).encode()).hexdigest()


class SpritesTargetState(NamedTargetState):
    desktop_phase: Literal["installing", "starting", "ready", "failed"] = "ready"
    install_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class SpriteInfo(BaseModel):
    id: str
    name: str
    status: str
    labels: list[str] = []
    url: str | None = Field(default=None, repr=False)


class SpritesReference(NativeReference[SpritesEnvironmentConfiguration, SpritesTargetState]):
    def __init__(
        self,
        config: SpritesEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
    ):
        super().__init__("sprites", config, environment_id, state, state_model=SpritesTargetState)
        assert isinstance(runtime.configuration, SpritesConnectionConfiguration)
        self.backend = runtime.configuration
        assert isinstance(runtime.credential, TokenCredential)
        self.token = runtime.credential.api_key
        self.http: NativeHTTP | None = None
        self.sprite_name = self.name
        self._info: SpriteInfo | None = None

    def transport(self) -> NativeHTTP:
        if self.http is None:
            self.http = NativeHTTP(
                self.provider_key,
                "https://api.sprites.dev",
                self.token.get_secret_value(),
                self.config.request_timeout_seconds,
            )
        return self.http

    @property
    def path(self) -> str:
        # External state uses a native name; managed names are stable ownership correlation.
        name = self.target.target_id if self.target else self.name
        return f"/v1/sprites/{quote(name, safe='')}"

    async def lookup(self) -> SpriteInfo | None:
        raw = await self.transport().request("GET", self.path, missing=True)
        if raw is None:
            return None
        info = decode_response(SpriteInfo, raw, self.provider_key)
        if info.name != (self.target.target_id if self.target else self.name) or not all(
            f"{key}={value}" in info.labels for key, value in self.labels.items()
        ):
            raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)
        if self.target and self.target.backing_id != info.id:
            raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)
        self._info = info
        self.observe(
            SpritesTargetState(
                desktop_phase=self.target.desktop_phase if self.target else "ready",
                install_revision=self.target.install_revision if self.target else None,
                backing_id=info.id,
                target_id=info.name,
                environment_id=self.owner,
                configuration_fingerprint=self.config.fingerprint,
            )
        )
        return info

    def desktop_endpoint(self) -> str:
        try:
            url = urlsplit(self._info.url if self._info and self._info.url else "")
            valid = (
                url.scheme == "https"
                and url.hostname
                and url.hostname.endswith(".sprites.app")
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
                and url.path in {"", "/"}
                and url.port in {None, 443}
            )
        except ValueError:
            raise failure(self.provider_key, "provider_route_invalid", Category.UNAVAILABLE) from None
        if not valid:
            raise failure(self.provider_key, "provider_route_invalid", Category.UNAVAILABLE)
        return url.geturl().rstrip("/")

    async def status(self) -> Literal["running", "stopped", "absent"]:
        info = await self.lookup()
        # Warm/cold Sprites automatically wake on exec and retain their disk.
        return "running" if info else "absent"

    async def close_transport(self) -> None:
        if self.http:
            await self.http.close()
