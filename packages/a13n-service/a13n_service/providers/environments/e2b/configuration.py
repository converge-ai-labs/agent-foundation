"""E2B lifecycle configuration owned by Service."""

from a13n_harness.providers.environment.e2b.configuration import E2BEnvironmentConfiguration
from pydantic import ConfigDict, Field


class E2BRecipe(E2BEnvironmentConfiguration):
    model_config = ConfigDict(
        json_schema_extra={"x-primary-fields": ["template", "timeout_seconds", "allow_internet_access"]}
    )
    timeout_seconds: int = Field(default=3600, ge=30, le=86_400, title="Sandbox timeout (seconds)")
