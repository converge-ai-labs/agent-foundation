"""Configuration for the optional dynamic Environment projection."""

from pydantic import BaseModel, ConfigDict


class DynamicEnvironmentConfiguration(BaseModel):
    """Definition-owned narrowing for the access-derived Environment tool surface."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    files_enabled: bool = True
    shell_enabled: bool = True
    computer_enabled: bool = True
    file_tools: frozenset[str] | None = None
    shell_tools: frozenset[str] | None = None
    computer_tools: frozenset[str] | None = None


__all__ = ["DynamicEnvironmentConfiguration"]
