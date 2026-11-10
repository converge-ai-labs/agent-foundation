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

    startup_command: list[str] | None = Field(
        default=None,
        min_length=1,
        title="Startup command",
        description='Required for preinstalled snapshots: operator-authored startup argv, for example ["/usr/local/bin/start-envd"]. It must start the runtime in the background and return. Service runs it unchanged as root on create and native session resume, with the instance bootstrap environment. Managed Envd uses the standard launcher.',
    )

    desktop_assistance: bool = Field(
        default=False,
        title="Desktop viewing and control",
        description="Automatic desktops include the access supervisor; custom snapshots must provide it.",
    )

    @model_validator(mode="after")
    def assistance_snapshot(self) -> Self:
        if self.execution != "native" and self.envd_management == "preinstalled":
            if self.snapshot in {None, "auto"} or self.startup_command is None:
                raise ValueError("Preinstalled Envd requires a custom snapshot and startup_command")
        if self.startup_command is not None and (self.execution == "native" or self.envd_management == "managed"):
            raise ValueError("startup_command requires preinstalled Envd execution")
        if self.desktop_assistance and (not self.desktop or not self.snapshot):
            raise ValueError("Desktop access requires desktop=True and an automatic or custom snapshot")
        return self

    @property
    def fingerprint(self) -> str:
        # Management-only policy must not change the identity used by execution attachment.
        return VercelEnvironmentConfiguration.model_validate(
            self.model_dump(exclude={"desktop_assistance", "envd_management", "startup_command"})
        ).fingerprint
