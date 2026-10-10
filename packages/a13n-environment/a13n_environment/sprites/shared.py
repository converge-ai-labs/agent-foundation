"""Sprites shared implementation."""

from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from ..errors import EnvironmentProviderErrorCategory as Category
from ..models import EnvironmentState
from ..native.configuration import CommandConfiguration, NamedTargetState, TokenCredential
from ..native.environment import NativeReference, NativeRuntime
from ..native.errors import failure
from ..native.http import NativeHTTP, decode_response


class SpritesConnectionConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    organization: str = Field(
        min_length=1, max_length=128, description="Fly.io organization owning the token and Sprites."
    )


class SpritesEnvironmentConfiguration(CommandConfiguration):
    model_config = ConfigDict(frozen=True, extra="forbid", json_schema_extra={"x-primary-fields": ["region"]})
    root: str = "/home/sprite"
    region: str | None = Field(default=None, max_length=64)


class SpriteInfo(BaseModel):
    id: str
    name: str
    status: str
    labels: list[str] = []


class SpritesReference(NativeReference[SpritesEnvironmentConfiguration, NamedTargetState]):
    def __init__(
        self,
        config: SpritesEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
    ):
        super().__init__("sprites", config, environment_id, state, state_model=NamedTargetState)
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
        if not all(f"{key}={value}" in info.labels for key, value in self.labels.items()):
            raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)
        if self.target and self.target.backing_id != info.id:
            raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)
        self._info = info
        self.observe(
            NamedTargetState(
                backing_id=info.id,
                target_id=info.name,
                environment_id=self.owner,
                configuration_fingerprint=self.config.fingerprint,
            )
        )
        return info

    async def status(self) -> Literal["running", "stopped", "absent"]:
        info = await self.lookup()
        # Warm/cold Sprites automatically wake on exec and retain their disk.
        return "running" if info else "absent"

    async def close_transport(self) -> None:
        if self.http:
            await self.http.close()
