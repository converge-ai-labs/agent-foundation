"""Sprites management implementation."""

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.native.configuration import NamedTargetState
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.http import decode_response
from a13n_harness.providers.environment.sprites.shared import (
    SpriteInfo,
    SpritesEnvironmentConfiguration,
    SpritesReference,
)

from a13n_service.providers.environment.native import NativeManagement


class SpritesManagement(SpritesReference, NativeManagement[SpritesEnvironmentConfiguration, NamedTargetState]):
    def __init__(
        self,
        config: SpritesEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
        operation_id: str,
    ):
        super().__init__(config, environment_id, state, runtime)
        del operation_id

    async def create(self) -> None:
        info = await self.lookup()
        if info is None:
            if self.target is not None:
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
            self.observe(
                NamedTargetState(
                    backing_id=info.id,
                    target_id=info.name,
                    environment_id=self.owner,
                    configuration_fingerprint=self.config.fingerprint,
                )
            )
        self._info = info

    async def start(self) -> None:
        if await self.lookup() is None:
            raise failure(self.provider_key, "provider_target_missing", Category.MISSING)

    async def stop(self) -> None:
        raise failure(self.provider_key, "provider_stop_unsupported", Category.UNSUPPORTED)

    async def destroy(self) -> None:
        if await self.lookup() is not None:
            await self.transport().request("DELETE", self.path, missing=True)

        await self.confirm_deleted(self.lookup)
