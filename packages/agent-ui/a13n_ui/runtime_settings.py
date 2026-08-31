"""Strict bounded settings for replaceable Agent UI runtime Runners."""

from pydantic import BaseModel, ConfigDict, Field


class RuntimeGenerationSettings(BaseModel):
    """Bounded lifecycle settings for replaceable runtime Runners."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    startup_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    command_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    drain_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    terminate_timeout_seconds: float = Field(default=3.0, gt=0, le=60)
    kill_timeout_seconds: float = Field(default=3.0, gt=0, le=60)
    max_message_bytes: int = Field(default=64 * 1024 * 1024, ge=1024, le=128 * 1024 * 1024)
    retained_generations: int = Field(default=16, ge=2, le=32)
    retained_diagnostics: int = Field(default=50, ge=1, le=100)


__all__ = ["RuntimeGenerationSettings"]
