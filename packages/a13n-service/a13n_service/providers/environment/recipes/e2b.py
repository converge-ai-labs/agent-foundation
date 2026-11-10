"""E2B lifecycle configuration owned by Service."""

from typing import Self

from a13n_harness.providers.environment.e2b.configuration import E2BEnvironmentConfiguration
from pydantic import ConfigDict, Field, model_validator

from a13n_service.settings import RENEWAL_HORIZON_SECONDS


class E2BRecipe(E2BEnvironmentConfiguration):
    template: str = Field(
        default="base",
        min_length=1,
        max_length=256,
        title="E2B template name or ID",
        description="Use auto for a Service-prepared desktop. Headless Envd prepares the selected template's actual sandbox when preparation=headless. Stock base may require a newer GNU/Linux template for the pinned daemon.",
    )

    @model_validator(mode="before")
    @classmethod
    def automatic_desktop(cls, value: object) -> object:
        if isinstance(value, dict):
            value = dict(value)
            if value.get("desktop") is True:
                value.setdefault("execution", "envd_http")
                value.setdefault("template", "auto")
                if value.get("template") == "auto":
                    value.setdefault("desktop_assistance", True)
            elif value.get("execution") in {"envd_http", "envd_ws"} and value.get("template", "base") == "base":
                value.setdefault("preparation", "headless")
        return value

    desktop_assistance: bool = Field(
        default=False,
        title="Desktop viewing and control",
        description="Enabled by default for automatic desktop templates. Custom templates must preinstall desktop access components.",
    )
    model_config = ConfigDict(
        json_schema_extra={
            "x-primary-fields": ["execution", "desktop", "template", "timeout_seconds", "allow_internet_access"]
        }
    )
    timeout_seconds: int = Field(default=3600, ge=30, le=86_400, title="Sandbox timeout (seconds)")

    @model_validator(mode="after")
    def assistance_template(self) -> Self:
        if self.preparation == "headless" and self.timeout_seconds < 960:
            raise ValueError("Headless preparation requires a sandbox timeout of at least 960 seconds")
        if self.template == "auto" and not self.desktop:
            raise ValueError("Automatic E2B templates require desktop=True")
        if self.desktop_assistance and (not self.desktop or self.template in {"desktop", "base"}):
            raise ValueError("Desktop access requires desktop=True and an automatic or compatible custom template")
        return self


class CloudRecipe(E2BRecipe):
    timeout_seconds: int = Field(default=3600, ge=RENEWAL_HORIZON_SECONDS, le=86_400, title="Sandbox timeout (seconds)")
