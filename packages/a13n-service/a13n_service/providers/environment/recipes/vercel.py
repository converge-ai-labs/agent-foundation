"""Service desktop preparation defaults; custom snapshots retain their behavior."""

from typing import Self

from a13n_harness.providers.environment.vercel.shared import VercelEnvironmentConfiguration
from pydantic import Field, model_validator


class VercelRecipe(VercelEnvironmentConfiguration):
    @model_validator(mode="before")
    @classmethod
    def automatic_desktop(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("desktop") is True:
            value = dict(value)
            value.setdefault("execution", "envd_http")
            value.setdefault("snapshot", "auto")
            if value.get("snapshot") == "auto":
                value.setdefault("desktop_assistance", True)
                # Automatic desktops must also work on Vercel Hobby accounts.
                value.setdefault("timeout_seconds", 2700)
        return value

    desktop_assistance: bool = Field(
        default=False,
        title="Desktop assistance and control",
        description="Automatic desktops include the access supervisor; custom snapshots must provide it.",
    )

    @model_validator(mode="after")
    def assistance_snapshot(self) -> Self:
        if self.desktop_assistance and (not self.desktop or not self.snapshot):
            raise ValueError("Desktop assistance requires desktop=True and an automatic or custom snapshot")
        return self

    @property
    def fingerprint(self) -> str:
        # Management-only policy must not change the identity used by execution attachment.
        return VercelEnvironmentConfiguration.model_validate(
            self.model_dump(exclude={"desktop_assistance"})
        ).fingerprint
