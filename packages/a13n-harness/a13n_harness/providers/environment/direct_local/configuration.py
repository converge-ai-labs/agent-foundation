from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MIB = 1024 * 1024
_GIB = 1024 * _MIB


class DirectLocalRootConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    read_only: bool = False

    @field_validator("path")
    @classmethod
    def _absolute_path(cls, value: Path) -> Path:
        expanded = value.expanduser()
        if "\x00" in str(expanded):
            raise ValueError("Direct Local root path must not contain NUL")
        if not expanded.is_absolute():
            raise ValueError("Direct Local root path must be absolute")
        return expanded


class DirectLocalShellProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: Annotated[str, Field(min_length=1, max_length=128)]
    executable: Path
    fixed_arguments: tuple[Annotated[str, Field(max_length=4096)], ...] = ()
    dialect: Literal["posix", "powershell"] = "posix"
    allow_login: bool = False

    @model_validator(mode="after")
    def _login_dialect(self) -> Self:
        if self.dialect == "powershell" and self.allow_login:
            raise ValueError("PowerShell profiles do not support login mode")
        return self

    @field_validator("profile_id")
    @classmethod
    def _trimmed_profile_id(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("shell profile ID must not contain surrounding whitespace")
        return value

    @field_validator("executable")
    @classmethod
    def _absolute_executable(cls, value: Path) -> Path:
        expanded = value.expanduser()
        if "\x00" in str(expanded):
            raise ValueError("shell profile executable must not contain NUL")
        if not expanded.is_absolute():
            raise ValueError("shell profile executable must be absolute")
        return expanded

    @field_validator("fixed_arguments")
    @classmethod
    def _valid_fixed_arguments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any("\x00" in argument for argument in value):
            raise ValueError("shell profile fixed arguments must not contain NUL")
        return value


class DirectLocalEnvironmentConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", json_schema_extra={"x-primary-fields": ["root"]})

    root: DirectLocalRootConfiguration
    shell_profiles: tuple[DirectLocalShellProfile, ...] = ()
    allowed_executables: frozenset[Path] = frozenset()
    inherit_environment: bool = False
    allowed_environment_keys: frozenset[Annotated[str, Field(min_length=1, max_length=128)]] | None = frozenset()
    allowed_ports: frozenset[Annotated[int, Field(ge=1, le=65535)]] = frozenset()
    max_value_bytes: Annotated[int, Field(gt=0)] = 64 * _MIB
    max_concurrent_processes: Annotated[int, Field(gt=0)] = 128
    max_wall_time_seconds: float = 24 * 60 * 60
    terminate_grace_seconds: float = 5.0
    max_buffer_bytes: Annotated[int, Field(gt=0)] = _MIB
    max_spool_bytes: Annotated[int, Field(gt=0)] = 64 * _GIB

    @classmethod
    def _trimmed_environment_id(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("environment_id must not contain surrounding whitespace")
        return value

    @field_validator("allowed_environment_keys")
    @classmethod
    def _trimmed_environment_keys(cls, value: frozenset[str] | None) -> frozenset[str] | None:
        if value is None:
            return None
        if any(key != key.strip() for key in value):
            raise ValueError("allowed environment keys must not contain surrounding whitespace")
        if any("\x00" in key for key in value):
            raise ValueError("allowed environment keys must not contain NUL")
        return value

    @field_validator("allowed_executables")
    @classmethod
    def _absolute_allowed_executables(cls, value: frozenset[Path]) -> frozenset[Path]:
        expanded = frozenset(path.expanduser() for path in value)
        if any("\x00" in str(path) for path in expanded):
            raise ValueError("allowed executables must not contain NUL")
        if any(not path.is_absolute() for path in expanded):
            raise ValueError("allowed executables must be absolute paths")
        return expanded

    @model_validator(mode="after")
    def _consistent_configuration(self) -> Self:
        profile_ids = tuple(profile.profile_id for profile in self.shell_profiles)
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("shell profile IDs must be unique")
        if self.root.read_only and (self.shell_profiles or self.allowed_executables):
            raise ValueError("read-only Direct Local roots cannot enable process execution")
        for name, value in (
            ("max_wall_time_seconds", self.max_wall_time_seconds),
            ("terminate_grace_seconds", self.terminate_grace_seconds),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        return self
