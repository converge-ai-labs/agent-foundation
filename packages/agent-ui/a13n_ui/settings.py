"""Restart-bound settings for one process-local Agent UI application."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


def default_agent_ui_root() -> Path:
    """Return the fixed platform-user root without cwd discovery."""

    return (Path.home() / ".a13n-ui").resolve(strict=False)


def default_agent_ui_data_root() -> Path:
    return default_agent_ui_root() / "data"


class StorageSettings(BaseModel):
    """Settings for one Agent UI data root."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    data_root: Path = Field(default_factory=default_agent_ui_data_root)
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
    """Bounded App behavior for the package-selected Local EIP runtime."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    executable: Path | None = None
    download_timeout_seconds: float = Field(default=120.0, gt=0, le=3600)
    command_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_archive_bytes: int = Field(default=512 * 1024 * 1024, ge=1024, le=2 * 1024 * 1024 * 1024)

    @field_validator("executable")
    @classmethod
    def _normalize_executable(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if "\x00" in str(value):
            raise ValueError("envd executable must not contain NUL")
        resolved = value.expanduser().resolve(strict=False)
        if resolved.name not in {"agent-envd", "agent-envd.exe"}:
            raise ValueError("envd executable must name agent-envd or agent-envd.exe")
        if resolved.exists() and resolved.is_dir():
            raise ValueError("envd executable must not be a directory")
        return resolved


class AgentUiSettings(BaseModel):
    """Process settings embedded in the selected Agent UI document."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    storage: StorageSettings = Field(default_factory=StorageSettings)
    envd_runtime: EnvdRuntimeSettings = Field(default_factory=EnvdRuntimeSettings)
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
    "StorageSettings",
    "default_agent_ui_data_root",
    "default_agent_ui_root",
]
