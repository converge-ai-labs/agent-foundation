"""Service-owned per-Sprite desktop installation and human-access opt-in."""

from typing import Self

from a13n_harness.providers.environment.sprites.shared import SpritesEnvironmentConfiguration
from pydantic import Field, model_validator

from ..envd_policy import EnvdRecipe


class SpritesRecipe(SpritesEnvironmentConfiguration, EnvdRecipe):
    desktop_assistance: bool = Field(default=False, title="Desktop viewing and control")

    @model_validator(mode="before")
    @classmethod
    def automatic_desktop(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("desktop") is True:
            value = dict(value)
            value.setdefault("execution", "envd_http")
            value.setdefault("desktop_assistance", True)
        if isinstance(value, dict) and value.get("execution") in {"envd_http", "envd_ws"}:
            value = dict(value)
            value.setdefault("envd_management", "managed")
        return value

    @property
    def fingerprint(self) -> str:
        return SpritesEnvironmentConfiguration.model_validate(
            self.model_dump(exclude={"envd_management", "desktop_assistance"})
        ).fingerprint

    @model_validator(mode="after")
    def assistance_requires_desktop(self) -> Self:
        if self.envd_management == "preinstalled" and self.execution == "envd_ws":
            raise ValueError("Preinstalled Sprites requires envd_http; outbound bootstrap requires managed Envd")
        if self.desktop_assistance and not self.desktop:
            raise ValueError("Desktop access requires desktop=True")
        return self
