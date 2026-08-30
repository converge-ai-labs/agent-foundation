"""Strict restart-bound settings for the local Agent UI application."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from a13n_ui.configuration.models import ConfigurationSettings
from a13n_ui.runtime_settings import RuntimeGenerationSettings


class StorageSettings(BaseModel):
    """Restart-bound settings for one Agent UI data root."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    data_root: Path
    busy_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    cleanup_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    max_object_bytes: int = Field(default=64 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024)

    @field_validator("data_root")
    @classmethod
    def _normalize_data_root(cls, value: Path) -> Path:
        if "\x00" in str(value):
            raise ValueError("data_root must not contain NUL")
        return value.expanduser().resolve(strict=False)


class EnvdRuntimeSettings(BaseModel):
    """Bounded Host behavior for the package-selected Local Sandbox runtime."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    download_timeout_seconds: float = Field(default=120.0, gt=0, le=3600)
    command_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_archive_bytes: int = Field(default=512 * 1024 * 1024, ge=1024, le=2 * 1024 * 1024 * 1024)


class AgentUiSettings(BaseModel):
    """Process settings required to construct the surface-neutral Host."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    storage: StorageSettings
    configuration: ConfigurationSettings = ConfigurationSettings()
    envd_runtime: EnvdRuntimeSettings = EnvdRuntimeSettings()
    runtime: RuntimeGenerationSettings = RuntimeGenerationSettings()
    shutdown_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    log_level: str = Field(default="INFO", min_length=1, max_length=32)
    log_format: str = Field(default="pretty", pattern="^(pretty|json)$")

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError("log_level must be a standard logging level")
        return normalized


__all__ = [
    "AgentUiSettings",
    "EnvdRuntimeSettings",
    "RuntimeGenerationSettings",
    "StorageSettings",
]
