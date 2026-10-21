"""Modal management-only opt-in for human desktop access."""

from typing import Self

from a13n_harness.providers.environment.modal.shared import ModalEnvironmentConfiguration
from pydantic import Field, model_validator


class ModalRecipe(ModalEnvironmentConfiguration):
    desktop_assistance: bool = Field(
        default=False,
        title="Desktop assistance and control",
        description="Requires a desktop image with the preinstalled desktop access supervisor.",
    )

    @model_validator(mode="after")
    def assistance_desktop(self) -> Self:
        if self.desktop_assistance and not self.desktop:
            raise ValueError("Desktop assistance requires desktop=True")
        return self

    @property
    def fingerprint(self) -> str:
        return ModalEnvironmentConfiguration.model_validate(self.model_dump(exclude={"desktop_assistance"})).fingerprint
