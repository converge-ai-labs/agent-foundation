"""Configuration for the optional dynamic Environment projection."""

from pydantic import BaseModel, ConfigDict


class DynamicEnvironmentConfiguration(BaseModel):
    """Configuration marker for the access-derived Environment tool surface."""

    model_config = ConfigDict(frozen=True, extra="forbid")


__all__ = ["DynamicEnvironmentConfiguration"]
