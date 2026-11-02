"""Service-owned per-Sprite desktop installation and human-access opt-in."""

from typing import Self

from a13n_harness.providers.environment.sprites.shared import SpritesEnvironmentConfiguration
from pydantic import Field, model_validator


class SpritesRecipe(SpritesEnvironmentConfiguration):
    desktop_assistance: bool = Field(default=False, title="Desktop assistance and control")

    @model_validator(mode="before")
    @classmethod
    def automatic_desktop(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("desktop") is True:
            value = dict(value)
            value.setdefault("execution", "envd_http")
            value.setdefault("desktop_assistance", True)
        return value

    @model_validator(mode="after")
    def assistance_requires_desktop(self) -> Self:
        if self.desktop_assistance and not self.desktop:
            raise ValueError("Desktop assistance requires desktop=True")
        return self
