from __future__ import annotations

import unicodedata
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MIB = 1024 * 1024
_GIB = 1024 * _MIB
_MAX_RESPONSE_BYTES = 16 * _MIB


class LocalEnvdNetworkMode(StrEnum):
    HOST = "host"
    DENY = "deny"


class LocalEnvdWorkspaceConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    read_only: bool = False

    @field_validator("path")
    @classmethod
    def _absolute_path(cls, value: Path) -> Path:
        return _absolute_os_path(value, label="Local Envd workspace")


class LocalEnvdShellProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: Annotated[str, Field(min_length=1, max_length=128)]
    executable: Path
    fixed_arguments: tuple[Annotated[str, Field(max_length=4096)], ...] = ()
    allow_login: bool = False
    max_script_bytes: Annotated[int, Field(gt=0)] = _MIB

    @field_validator("profile_id")
    @classmethod
    def _valid_profile_id(cls, value: str) -> str:
        if value != value.strip() or not all(
            character.isascii() and (character.isalnum() or character in ".-_") for character in value
        ):
            raise ValueError(
                "shell profile ID must use ASCII letters, digits, dot, dash, or underscore without surrounding whitespace"
            )
        return value

    @field_validator("executable")
    @classmethod
    def _absolute_executable(cls, value: Path) -> Path:
        return _absolute_os_path(value, label="shell profile executable")

    @field_validator("fixed_arguments")
    @classmethod
    def _valid_fixed_arguments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any("\x00" in argument for argument in value):
            raise ValueError("shell profile fixed arguments must not contain NUL")
        return value


class LocalEnvdEnvironmentConfiguration(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", json_schema_extra={"x-primary-fields": ["workspace", "execution_network"]}
    )

    workspace: LocalEnvdWorkspaceConfiguration
    execution_network: LocalEnvdNetworkMode = LocalEnvdNetworkMode.HOST
    trusted_executable_roots: tuple[Path, ...] = ()
    shell_profiles: tuple[LocalEnvdShellProfile, ...] = ()
    max_file_bytes: Annotated[int, Field(gt=0)] = 16 * _MIB
    max_output_preview_bytes: Annotated[int, Field(gt=0, le=_MAX_RESPONSE_BYTES)] = 64 * 1024
    max_output_bytes_per_stream: Annotated[int, Field(gt=0)] = _GIB
    max_spool_bytes: Annotated[int, Field(gt=0)] = 64 * _GIB

    @classmethod
    def _valid_environment_id(cls, value: str) -> str:
        if value != value.strip() or any(unicodedata.category(character) == "Cc" for character in value):
            raise ValueError("environment_id must be trimmed and contain no control characters")
        return value

    @field_validator("trusted_executable_roots")
    @classmethod
    def _absolute_trusted_roots(cls, value: tuple[Path, ...]) -> tuple[Path, ...]:
        return tuple(_absolute_os_path(path, label="trusted executable root") for path in value)

    @model_validator(mode="after")
    def _consistent_configuration(self) -> Self:
        profile_ids = tuple(profile.profile_id for profile in self.shell_profiles)
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("shell profile IDs must be unique")
        if len(self.trusted_executable_roots) != len(set(self.trusted_executable_roots)):
            raise ValueError("trusted executable roots must be unique")
        if self.max_output_preview_bytes > self.max_output_bytes_per_stream:
            raise ValueError("max_output_preview_bytes must not exceed max_output_bytes_per_stream")
        if self.max_spool_bytes < self.max_output_bytes_per_stream * 2:
            raise ValueError("max_spool_bytes must reserve both output streams")
        return self


def _absolute_os_path(value: Path, *, label: str) -> Path:
    expanded = value.expanduser()
    if "\x00" in str(expanded):
        raise ValueError(f"{label} path must not contain NUL")
    if not expanded.is_absolute():
        raise ValueError(f"{label} path must be absolute")
    return expanded
