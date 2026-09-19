"""Sprites HTTP lifecycle and binary WebSocket exec protocol."""

import asyncio
import json
from typing import Literal
from urllib.parse import quote, urlencode

from pydantic import BaseModel, ConfigDict, Field
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import EnvironmentProviderOutcomeCertainty as Certainty
from ..models import EnvironmentState
from ..native.configuration import CommandConfiguration, NamedTargetState, TokenCredential
from ..native.environment import NativeEnvironment, NativeRuntime, native_definition
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


class SpritesEnvironment(NativeEnvironment[SpritesEnvironmentConfiguration, NamedTargetState]):
    def __init__(
        self,
        config: SpritesEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
    ):
        super().__init__(
            "sprites", config, environment_id, state, managed=runtime.managed, state_model=NamedTargetState
        )
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
        name = self.target.target_id if not self.managed and self.target else self.name
        return f"/v1/sprites/{quote(name, safe='')}"

    async def inspect(self) -> SpriteInfo | None:
        raw = await self.transport().request("GET", self.path, missing=True)
        if raw is None:
            return None
        info = decode_response(SpriteInfo, raw, self.provider_key)
        if self.managed and not all(f"{key}={value}" in info.labels for key, value in self.labels.items()):
            raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)
        if self.target and self.target.backing_id != info.id:
            raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)
        self._info = info
        self.remember(
            NamedTargetState(
                backing_id=info.id,
                target_id=info.name,
                environment_id=self.owner,
                configuration_fingerprint=self.config.fingerprint,
            )
        )
        return info

    async def _prepare(self, *, mount_id: str) -> None:
        info = await self.inspect()
        if info is None:
            if not self.managed:
                raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
            raw = await self.transport().request(
                "POST",
                "/v1/sprites",
                body={
                    "name": self.name,
                    "labels": [f"{key}={value}" for key, value in self.labels.items()],
                    "config": {"region": self.config.region} if self.config.region else {},
                    "url_settings": {"auth": "sprite"},
                },
            )
            info = decode_response(SpriteInfo, raw, self.provider_key, mutation=True)
            self.remember(
                NamedTargetState(
                    backing_id=info.id,
                    target_id=info.name,
                    environment_id=self.owner,
                    configuration_fingerprint=self.config.fingerprint,
                )
            )
        self._info = info
        await self.open_operations(self.execute, info.id, mount_id)

    async def execute(self, argv: list[str], timeout: float) -> str:
        launcher = [self.config.python, "-I", "-c", "import json,os,sys; a=json.load(sys.stdin); os.execvp(a[0],a)"]
        query = urlencode([*(("cmd", arg) for arg in launcher), ("path", launcher[0]), ("stdin", "true")])
        url = "wss://api.sprites.dev" + self.path + "/exec?" + query
        output = bytearray()
        try:
            async with asyncio.timeout(timeout):
                async with connect(
                    url,
                    additional_headers={"Authorization": f"Bearer {self.token.get_secret_value()}"},
                    max_size=1024 * 1024,
                    close_timeout=1,
                ) as socket:
                    await socket.send(b"\x00" + json.dumps(argv).encode())
                    await socket.send(b"\x04")
                    async for message in socket:
                        if isinstance(message, bytes):
                            if not message:
                                continue
                            channel, payload = message[0], message[1:]
                            if channel == 1:
                                output.extend(payload)
                                if len(output) > 24 * 1024 * 1024:
                                    raise failure(
                                        self.provider_key,
                                        "provider_response_invalid",
                                        Category.UNKNOWN_OUTCOME,
                                        certainty=Certainty.UNKNOWN,
                                    )
                            elif channel == 3:
                                if len(payload) != 1:
                                    break
                                if payload[0] != 0:
                                    raise failure(
                                        self.provider_key,
                                        "provider_command_failed",
                                        Category.PROVIDER_FAILURE,
                                        certainty=Certainty.KNOWN,
                                    )
                                return output.decode()
                        else:
                            event = json.loads(message)
                            if event.get("type") == "exit":
                                if type(event["exit_code"]) is not int:
                                    raise ValueError("Invalid native exit code")
                                if event["exit_code"] != 0:
                                    raise failure(
                                        self.provider_key,
                                        "provider_command_failed",
                                        Category.PROVIDER_FAILURE,
                                        certainty=Certainty.KNOWN,
                                    )
                                return output.decode()
                            if event.get("type") == "error":
                                break
        except (WebSocketException, TimeoutError, OSError, ValueError, TypeError, KeyError):
            pass
        raise failure(
            self.provider_key, "provider_unknown_outcome", Category.UNKNOWN_OUTCOME, certainty=Certainty.UNKNOWN
        )

    async def reconcile(self) -> Literal["running", "stopped", "absent"]:
        info = await self.inspect()
        # Warm/cold Sprites automatically wake on exec and retain their disk.
        return "running" if info else "absent"

    async def _stop(self) -> None:
        raise failure(self.provider_key, "provider_stop_unsupported", Category.UNSUPPORTED)

    async def _destroy(self) -> None:
        if not self.managed:
            raise failure(self.provider_key, "provider_denied", Category.DENIED)
        if await self.inspect() is not None:
            await self.transport().request("DELETE", self.path, missing=True)

        await self.confirm_deleted(self.inspect)

    async def close_transport(self) -> None:
        if self.http:
            await self.http.close()


SPRITES = native_definition(
    type="sprites",
    display_name="Fly.io Sprites",
    configuration_model=SpritesConnectionConfiguration,
    credential_model=TokenCredential,
    environment_model=SpritesEnvironmentConfiguration,
    state_model=NamedTargetState,
    environment_type=SpritesEnvironment,
    supports_stop=False,
    requires_keepalive=False,
)
