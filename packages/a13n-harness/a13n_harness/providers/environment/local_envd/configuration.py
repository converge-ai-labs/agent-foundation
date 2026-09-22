from __future__ import annotations

from pathlib import Path
from typing import Annotated, Self

from a13n_envd_client.eip.v1 import DisabledSandbox, SandboxPolicy
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..envd_policy import EnvdExecutionConfiguration, EnvdNetworkConfiguration
from ..remote_envd.configuration import RemoteEnvdEnvironmentConfiguration

_MIB = 1024 * 1024
_GIB = 1024 * _MIB
_MAX_RESPONSE_BYTES = 16 * _MIB


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


class LocalEnvdEnvironmentConfiguration(RemoteEnvdEnvironmentConfiguration):
    """One fixed-cwd Session selection; never a daemon launch policy."""


class LocalEnvdLaunchConfiguration(BaseModel):
    """Host-selected daemon recipe, shared by every Session on this runtime."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution: EnvdExecutionConfiguration = Field(default_factory=EnvdExecutionConfiguration)
    sandbox: SandboxPolicy = Field(default_factory=lambda: DisabledSandbox(mode="disabled"))
    egress: EnvdNetworkConfiguration = Field(default_factory=lambda: EnvdNetworkConfiguration(mode="inherit"))
    default_working_directory: Path | None = None
    directory_discovery: bool = True
    trusted_executable_roots: tuple[Path, ...] = ()
    shell_profiles: tuple[LocalEnvdShellProfile, ...] = ()
    max_file_bytes: Annotated[int, Field(gt=0)] = 16 * _MIB
    max_output_preview_bytes: Annotated[int, Field(gt=0, le=_MAX_RESPONSE_BYTES)] = 64 * 1024
    max_output_bytes_per_stream: Annotated[int, Field(gt=0)] = 256 * _MIB
    max_spool_bytes: Annotated[int, Field(gt=0)] = _GIB
    max_device_spool_bytes: Annotated[int, Field(gt=0)] = 4 * _GIB

    @field_validator("default_working_directory")
    @classmethod
    def _default_directory(cls, value: Path | None) -> Path | None:
        return None if value is None else _absolute_os_path(value, label="default working directory")

    @field_validator("trusted_executable_roots")
    @classmethod
    def _absolute_trusted_roots(cls, value: tuple[Path, ...]) -> tuple[Path, ...]:
        return tuple(_absolute_os_path(path, label="trusted executable root") for path in value)

    @model_validator(mode="after")
    def _consistent_configuration(self) -> Self:
        if (self.execution.uid is None) != (self.execution.gid is None):
            raise ValueError("execution.uid and execution.gid must be configured together")
        profile_ids = tuple(profile.profile_id for profile in self.shell_profiles)
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("shell profile IDs must be unique")
        if len(self.trusted_executable_roots) != len(set(self.trusted_executable_roots)):
            raise ValueError("trusted executable roots must be unique")
        if self.max_output_preview_bytes > self.max_output_bytes_per_stream:
            raise ValueError("max_output_preview_bytes must not exceed max_output_bytes_per_stream")
        if self.max_spool_bytes > self.max_device_spool_bytes:
            raise ValueError("Session spool capacity must not exceed Device capacity")
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
