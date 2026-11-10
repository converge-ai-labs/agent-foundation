"""Modal management recipes and automatic Envd profiles."""

from typing import Self

from a13n_harness.providers.environment.modal.shared import ModalEnvironmentConfiguration
from pydantic import Field, model_validator


class ModalRecipe(ModalEnvironmentConfiguration):
    image: str = Field(
        default="auto",
        min_length=1,
        max_length=256,
        description="Automatic headless or desktop Envd image, or a compatible custom registry image / Modal image ID.",
    )

    base_image: str | None = Field(
        default=None,
        max_length=512,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/:-]*@sha256:[a-f0-9]{64}$",
        description="Explicit managed preparation base for image=auto: digest-pinned Debian 13 or Ubuntu 24.04+ GNU/Linux x86_64, root build user, and free sandbox UID/GID 1000. Preparation replaces its entrypoint and installs the selected profile.",
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
        title="Desktop viewing and control",
        description="Enable human desktop control. The automatic image includes the required supervisor.",
    )

    @model_validator(mode="after")
    def assistance_desktop(self) -> Self:
        if self.base_image is not None and self.image != "auto":
            raise ValueError("base_image requires image=auto; explicit images remain preinstalled and unchanged")
        if self.desktop_assistance and not self.desktop:
            raise ValueError("Desktop access requires desktop=True")
        return self

    @property
    def fingerprint(self) -> str:
        return ModalEnvironmentConfiguration.model_validate(
            self.model_dump(exclude={"desktop_assistance", "base_image"})
        ).fingerprint
