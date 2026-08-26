"""Strict restart-bound settings for the local Agent UI application."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from converge_agent_ui.configuration.models import ConfigurationSettings


class DurabilityProfile(StrEnum):
    """Local persistence durability selected for one application lifetime."""

    full = "full"


class StorageSettings(BaseModel):
    """Restart-bound settings for one Agent UI data root."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    data_root: Path
    durability_profile: DurabilityProfile = DurabilityProfile.full
    busy_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    cleanup_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    lease_heartbeat_seconds: float = Field(default=2.0, gt=0, le=60)
    max_object_bytes: int = Field(default=64 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024)
    max_staging_entries: int = Field(default=1024, gt=0, le=100_000)

    @field_validator("data_root")
    @classmethod
    def _normalize_data_root(cls, value: Path) -> Path:
        if "\x00" in str(value):
            raise ValueError("data_root must not contain NUL")
        return value.expanduser().resolve(strict=False)


class AgentUiSettings(BaseModel):
    """Process settings required to construct the surface-neutral application."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    storage: StorageSettings
    configuration: ConfigurationSettings = ConfigurationSettings()
    process_settings_path: Path | None = None
    shutdown_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    log_level: str = Field(default="INFO", min_length=1, max_length=32)
    log_format: str = Field(default="pretty", pattern="^(pretty|json)$")

    @field_validator("process_settings_path")
    @classmethod
    def _normalize_process_settings_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if "\x00" in str(value) or not value.expanduser().is_absolute():
            raise ValueError("process_settings_path must be an absolute YAML or JSON path")
        expanded = value.expanduser()
        if expanded.suffix.lower() not in {".yaml", ".yml", ".json"}:
            raise ValueError("process_settings_path must use YAML or JSON")
        return expanded.resolve(strict=False)

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError("log_level must be a standard logging level")
        return normalized


__all__ = ["AgentUiSettings", "DurabilityProfile", "StorageSettings"]
