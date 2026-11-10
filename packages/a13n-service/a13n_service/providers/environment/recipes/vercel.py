"""Service desktop preparation defaults; custom snapshots retain their behavior."""

from typing import Self

from a13n_harness.providers.environment.vercel.shared import VercelEnvironmentConfiguration
from pydantic import Field, model_validator

from ..envd_policy import EnvdRecipe


class VercelRecipe(VercelEnvironmentConfiguration, EnvdRecipe):
    @model_validator(mode="before")
    @classmethod
    def automatic_desktop(cls, value: object) -> object:
        if isinstance(value, dict) and (
            value.get("desktop") is True or value.get("execution") in {"envd_http", "envd_ws"}
        ):
            value = dict(value)
            value.setdefault("execution", "envd_http")
            value.setdefault("snapshot", "auto")
            if value.get("snapshot") in {None, "auto"}:
                value.setdefault("desktop_assistance", value.get("desktop", False))
                value.setdefault("envd_management", "managed")
                # Automatic targets must also work on Vercel Hobby accounts.
                value.setdefault("timeout_seconds", 2700)
        return value

    desktop_assistance: bool = Field(
        default=False,
        title="Desktop viewing and control",
        description="Automatic desktops include the access supervisor; custom snapshots must provide it.",
    )

    @model_validator(mode="after")
    def assistance_snapshot(self) -> Self:
        if self.execution != "native" and self.envd_management == "preinstalled" and self.snapshot in {None, "auto"}:
            raise ValueError("Preinstalled Envd requires a custom snapshot")
        if self.desktop_assistance and (not self.desktop or not self.snapshot):
            raise ValueError("Desktop access requires desktop=True and an automatic or custom snapshot")
        return self

    @property
    def fingerprint(self) -> str:
        # Management-only policy must not change the identity used by execution attachment.
        return VercelEnvironmentConfiguration.model_validate(
            self.model_dump(exclude={"desktop_assistance", "envd_management"})
        ).fingerprint
