"""Modal management recipes and automatic desktop defaults."""

from typing import Self

from a13n_harness.providers.environment.modal.shared import ModalEnvironmentConfiguration
from pydantic import Field, model_validator


class ModalRecipe(ModalEnvironmentConfiguration):
    image: str = Field(
        default="auto",
        min_length=1,
        max_length=256,
        description="Automatic desktop image, or a compatible custom registry image / Modal image ID.",
    )

    @model_validator(mode="before")
    @classmethod
    def automatic_desktop(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("desktop") is True and value.get("image", "auto") == "auto":
            value = dict(value)
            value.setdefault("desktop_assistance", True)
            value.setdefault("cpu", 2)
            value.setdefault("memory", 4096)
            value.setdefault("timeout_seconds", 3600)
        return value

    desktop_assistance: bool = Field(
        default=False,
        title="Desktop assistance and control",
        description="Enable human desktop control. The automatic image includes the required supervisor.",
    )

    @model_validator(mode="after")
    def assistance_desktop(self) -> Self:
        if self.image == "auto" and not self.desktop:
            raise ValueError(
                "Automatic Modal images require desktop=True; headless environments require a custom image"
            )
        if self.desktop_assistance and not self.desktop:
            raise ValueError("Desktop assistance requires desktop=True")
        return self

    @property
    def fingerprint(self) -> str:
        return ModalEnvironmentConfiguration.model_validate(self.model_dump(exclude={"desktop_assistance"})).fingerprint
