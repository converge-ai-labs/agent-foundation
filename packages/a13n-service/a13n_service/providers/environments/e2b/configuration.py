"""E2B lifecycle configuration owned by Service."""

from typing import Self

from a13n_harness.providers.environment.e2b.configuration import E2BEnvironmentConfiguration
from pydantic import ConfigDict, Field, model_validator


class E2BRecipe(E2BEnvironmentConfiguration):
    desktop_assistance: bool = Field(
        default=False,
        title="Agent-requested desktop assistance",
        description="Requires an operator-built desktop access template. Does not install or enable a viewer in stock templates.",
    )
    model_config = ConfigDict(
        json_schema_extra={
            "x-primary-fields": ["execution", "desktop", "template", "timeout_seconds", "allow_internet_access"]
        }
    )
    timeout_seconds: int = Field(default=3600, ge=30, le=86_400, title="Sandbox timeout (seconds)")

    @model_validator(mode="after")
    def assistance_template(self) -> Self:
        if self.desktop_assistance and (not self.desktop or self.template in {"desktop", "base"}):
            raise ValueError("Desktop assistance requires desktop=True and a compatible operator-built template")
        return self
