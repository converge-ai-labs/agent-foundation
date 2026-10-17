"""Configuration for the optional dynamic Environment projection."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

type ComputerCoordinateSpace = Literal["pixels", "normalized_1000"]


class ComputerConfiguration(BaseModel):
    """Explicit model-facing coordinates; providers always receive image pixels."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    coordinate_space: ComputerCoordinateSpace = Field(
        default="pixels",
        description="Pointer coordinates: screenshot pixels, or relative integers from 0 to 1000 on each axis.",
    )


class DynamicEnvironmentConfiguration(BaseModel):
    """Definition-owned narrowing for the access-derived Environment tool surface."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    files_enabled: bool = True
    shell_enabled: bool = True
    computer_enabled: bool = True
    computer_coordinate_space: ComputerCoordinateSpace = "pixels"
    file_tools: frozenset[str] | None = None
    shell_tools: frozenset[str] | None = None
    computer_tools: frozenset[str] | None = None


__all__ = ["ComputerConfiguration", "ComputerCoordinateSpace", "DynamicEnvironmentConfiguration"]
