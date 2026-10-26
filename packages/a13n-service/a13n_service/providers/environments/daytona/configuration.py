"""Service desktop preparation defaults; explicit custom snapshots retain their behavior."""

from collections.abc import Mapping
from typing import Self

from a13n_harness.providers.environment.daytona.shared import DaytonaEnvironmentConfiguration
from pydantic import Field, model_validator


class DaytonaRecipe(DaytonaEnvironmentConfiguration):
    snapshot: str = Field(
        default="auto",
        min_length=1,
        max_length=256,
        description="Use auto to build and reuse an Envd desktop snapshot, or supply a custom snapshot.",
    )
    desktop_assistance: bool = Field(
        default=False,
        title="Desktop assistance and control",
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
        if self.snapshot == "auto" and not self.desktop:
            raise ValueError("Automatic snapshots require desktop=True")
        if self.desktop_assistance and (not self.desktop or not self.snapshot):
            raise ValueError("Desktop assistance requires desktop=True and an operator-built snapshot")
        return self

    @property
    def fingerprint(self) -> str:
        # Management-only policy must not change the identity used by execution attachment.
        return DaytonaEnvironmentConfiguration.model_validate(
            self.model_dump(exclude={"desktop_assistance"})
        ).fingerprint
