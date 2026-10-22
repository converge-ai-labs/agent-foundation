"""Service-only opt-in for an operator-built Vercel desktop snapshot."""

from typing import Self

from a13n_harness.providers.environment.vercel.shared import VercelEnvironmentConfiguration
from pydantic import Field, model_validator


class VercelRecipe(VercelEnvironmentConfiguration):
    desktop_assistance: bool = Field(
        default=False,
        title="Desktop assistance and control",
        description="Requires a custom snapshot with the preinstalled desktop access supervisor.",
    )

    @model_validator(mode="after")
    def assistance_snapshot(self) -> Self:
        if self.desktop_assistance and (not self.desktop or not self.snapshot):
            raise ValueError("Desktop assistance requires desktop=True and an operator-built snapshot")
        return self

    @property
    def fingerprint(self) -> str:
        # Management-only policy must not change the identity used by execution attachment.
        return VercelEnvironmentConfiguration.model_validate(
            self.model_dump(exclude={"desktop_assistance"})
        ).fingerprint
