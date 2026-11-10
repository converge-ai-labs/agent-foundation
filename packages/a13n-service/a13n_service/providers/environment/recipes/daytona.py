"""Service Envd preparation defaults; explicit custom snapshots retain their behavior."""

from collections.abc import Mapping
from typing import Self

from a13n_harness.providers.environment.daytona.shared import DaytonaEnvironmentConfiguration
from pydantic import Field, model_validator


class DaytonaRecipe(DaytonaEnvironmentConfiguration):
    snapshot: str = Field(
        default="auto",
        min_length=1,
        max_length=256,
        description="Use auto to build a headless or desktop Envd snapshot. Custom snapshots must preinstall compatible Envd and start it at boot.",
    )
    base_image: str | None = Field(
        default=None,
        max_length=512,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/:-]*@sha256:[a-f0-9]{64}$",
        description="Explicit managed preparation base for snapshot=auto: digest-pinned Debian 13 or Ubuntu 24.04+ GNU/Linux x86_64, root build user, and free sandbox UID/GID 1000. Preparation replaces its boot entrypoint and installs the selected profile.",
    )
    desktop_assistance: bool = Field(
        default=False,
        title="Desktop viewing and control",
        description="Automatic desktops include the access supervisor; custom snapshots must provide it.",
    )

    @model_validator(mode="before")
    @classmethod
    def automatic_defaults(cls, value: object) -> object:
        if isinstance(value, Mapping) and value.get("desktop") is True and value.get("snapshot", "auto") == "auto":
            return {"desktop_assistance": True, **value}
        return value

    @model_validator(mode="after")
    def assistance_snapshot(self) -> Self:
        if self.base_image is not None and self.snapshot != "auto":
            raise ValueError("base_image requires snapshot=auto; explicit snapshots own preinstallation and boot")
        if self.desktop_assistance and (not self.desktop or not self.snapshot):
            raise ValueError("Desktop access requires desktop=True and an operator-built snapshot")
        return self

    @property
    def fingerprint(self) -> str:
        # Management-only policy must not change the identity used by execution attachment.
        return DaytonaEnvironmentConfiguration.model_validate(
            self.model_dump(exclude={"desktop_assistance", "base_image"})
        ).fingerprint
