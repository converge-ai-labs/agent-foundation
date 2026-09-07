from __future__ import annotations

import unicodedata
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MIB = 1024 * 1024
_GIB = 1024 * _MIB
_MAX_RESPONSE_BYTES = 16 * _MIB
_RESERVED_CONTAINER_TREES = (
    PurePosixPath("/run/a13n"),
    PurePosixPath("/home/sandbox/.local/state/a13n-envd"),
)
DEFAULT_DOCKER_IMAGE = "ghcr.io/converge-ai-labs/a13n-sandbox:latest"


class DockerImagePullPolicy(StrEnum):
    IF_MISSING = "if_missing"
    ALWAYS = "always"
    NEVER = "never"


class DockerBindMountSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["bind"] = "bind"
    path: Path

    @field_validator("path")
    @classmethod
    def _absolute_path(cls, value: Path) -> Path:
        expanded = value.expanduser()
        if "\x00" in str(expanded) or not expanded.is_absolute():
            raise ValueError("Docker bind source must be an absolute path without NUL")
        return expanded


class DockerVolumeMountSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["volume"] = "volume"
    name: Annotated[str, Field(min_length=1, max_length=255)]

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if (
            value != value.strip()
            or not value[0].isalnum()
            or not all(character.isascii() and (character.isalnum() or character in "_.-") for character in value)
        ):
            raise ValueError("Docker volume name must use ASCII letters, digits, dot, dash, or underscore")
        return value


DockerMountSource = Annotated[
    DockerBindMountSource | DockerVolumeMountSource,
    Field(discriminator="kind"),
]


class DockerMountConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mount_id: Annotated[str, Field(min_length=1, max_length=128)]
    container_path: PurePosixPath
    source: DockerMountSource | None = None
    read_only: bool = False
    allow_command_execution: bool = True

    @field_validator("mount_id")
    @classmethod
    def _valid_mount_id(cls, value: str) -> str:
        return _bounded_identifier(value, label="mount ID")

    @field_validator("container_path")
    @classmethod
    def _valid_container_path(cls, value: PurePosixPath) -> PurePosixPath:
        return _container_path(value, label="mount")


class DockerShellProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: Annotated[str, Field(min_length=1, max_length=128)]
    executable: PurePosixPath
    fixed_arguments: tuple[Annotated[str, Field(max_length=4096)], ...] = ()
    allow_login: bool = False
    max_script_bytes: Annotated[int, Field(gt=0)] = _MIB

    @field_validator("profile_id")
    @classmethod
    def _valid_profile_id(cls, value: str) -> str:
        return _bounded_identifier(value, label="shell profile ID")

    @field_validator("executable")
    @classmethod
    def _valid_executable(cls, value: PurePosixPath) -> PurePosixPath:
        return _container_path(value, label="shell executable")

    @field_validator("fixed_arguments")
    @classmethod
    def _valid_arguments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any("\x00" in argument or _has_control(argument) for argument in value):
            raise ValueError("shell fixed arguments must contain no NUL or control characters")
        return value


def _container_path(value: PurePosixPath, *, label: str) -> PurePosixPath:
    text = str(value)
    if "\x00" in text or not value.is_absolute() or ".." in value.parts:
        raise ValueError(f"Docker {label} path must be an absolute normalized POSIX path without NUL")
    if any(
        value == reserved or value in reserved.parents or reserved in value.parents
        for reserved in _RESERVED_CONTAINER_TREES
    ):
        raise ValueError(f"Docker {label} path must not overlap a provider-owned runtime tree")
    return value


def _bounded_identifier(value: str, *, label: str) -> str:
    if value != value.strip() or not all(
        character.isascii() and (character.isalnum() or character in ".-_") for character in value
    ):
        raise ValueError(f"Docker {label} must use ASCII letters, digits, dot, dash, or underscore")
    return value


def _has_control(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


_DEFAULT_MOUNTS = (
    DockerMountConfiguration(
        mount_id="workspace",
        container_path=PurePosixPath("/workspace"),
    ),
)
_DEFAULT_SHELL_PROFILES = (
    DockerShellProfile(
        profile_id="bash",
        executable=PurePosixPath("/bin/bash"),
        fixed_arguments=("-c",),
    ),
)


class DockerProviderConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    image: Annotated[str, Field(min_length=1, max_length=1024)] = DEFAULT_DOCKER_IMAGE
    pull_policy: DockerImagePullPolicy = DockerImagePullPolicy.IF_MISSING
    root_mount_id: Annotated[str, Field(min_length=1, max_length=128)] = "workspace"
    mounts: tuple[DockerMountConfiguration, ...] = _DEFAULT_MOUNTS
    trusted_executable_roots: tuple[PurePosixPath, ...] = ()
    shell_profiles: tuple[DockerShellProfile, ...] = _DEFAULT_SHELL_PROFILES
    nano_cpus: Annotated[int, Field(gt=0)] | None = None
    memory_bytes: Annotated[int, Field(gt=0)] | None = None
    pids_limit: Annotated[int, Field(gt=0)] | None = None
    stop_grace_seconds: Annotated[int, Field(ge=0, le=300)] = 10
    max_file_bytes: Annotated[int, Field(gt=0)] = 16 * _MIB
    max_output_preview_bytes: Annotated[int, Field(gt=0, le=_MAX_RESPONSE_BYTES)] = 64 * 1024
    max_output_bytes_per_stream: Annotated[int, Field(gt=0)] = _GIB
    max_spool_bytes: Annotated[int, Field(gt=0)] = 64 * _GIB

    @classmethod
    def _valid_environment_id(cls, value: str) -> str:
        if value != value.strip() or _has_control(value):
            raise ValueError("environment_id must be trimmed and contain no control characters")
        return value

    @field_validator("image")
    @classmethod
    def _valid_image(cls, value: str) -> str:
        if value != value.strip() or _has_control(value):
            raise ValueError("Docker image reference must be trimmed and contain no control characters")
        return value

    @field_validator("root_mount_id")
    @classmethod
    def _valid_root_mount_id(cls, value: str) -> str:
        return _bounded_identifier(value, label="root mount ID")

    @field_validator("trusted_executable_roots")
    @classmethod
    def _valid_trusted_roots(cls, value: tuple[PurePosixPath, ...]) -> tuple[PurePosixPath, ...]:
        return tuple(_container_path(path, label="trusted executable root") for path in value)

    @model_validator(mode="after")
    def _consistent_configuration(self) -> Self:
        if not self.mounts:
            raise ValueError("Docker configuration requires at least one mount")
        mount_ids = tuple(mount.mount_id for mount in self.mounts)
        mount_paths = tuple(mount.container_path for mount in self.mounts)
        profile_ids = tuple(profile.profile_id for profile in self.shell_profiles)
        if len(mount_ids) != len(set(mount_ids)):
            raise ValueError("Docker mount IDs must be unique")
        if len(mount_paths) != len(set(mount_paths)):
            raise ValueError("Docker container mount paths must be unique")
        if self.root_mount_id not in mount_ids:
            raise ValueError("root_mount_id must identify one configured Docker mount")
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("Docker shell profile IDs must be unique")
        if len(self.trusted_executable_roots) != len(set(self.trusted_executable_roots)):
            raise ValueError("Docker trusted executable roots must be unique")
        if self.max_output_preview_bytes > self.max_output_bytes_per_stream:
            raise ValueError("max_output_preview_bytes must not exceed max_output_bytes_per_stream")
        if self.max_spool_bytes < self.max_output_bytes_per_stream * 2:
            raise ValueError("max_spool_bytes must reserve both output streams")
        return self


class DockerTargetConfiguration(DockerProviderConfiguration):
    """Validated recipe combined with Host runtime identity; never a template payload."""

    environment_id: Annotated[str, Field(min_length=1, max_length=128)]


class DockerProviderStateData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment_id: Annotated[str, Field(min_length=1, max_length=128)]
    container_id: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    image_id: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    bootstrap_correlation: Annotated[str, Field(pattern=r"^bootstrap-[0-9a-f]{24}$")]
    configuration_fingerprint: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    create_correlation: Annotated[str, Field(pattern=r"^create-[0-9a-f]{24}$")]
