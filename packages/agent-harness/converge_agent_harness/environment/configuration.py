"""Configuration for the optional dynamic Environment projection."""

from pydantic import BaseModel, ConfigDict, Field


class DynamicEnvironmentConfiguration(BaseModel):
    """Definition-selected tool surfaces and finite run-local reference limits."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file_tools: bool = True
    shell_tools: bool = True
    process_tools: bool = True
    port_tools: bool = False
    max_reference_entries: int = Field(gt=0, le=100_000)


__all__ = ["DynamicEnvironmentConfiguration"]
