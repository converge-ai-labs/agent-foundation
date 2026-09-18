"""Shared bounds for native command-backed files and foreground execution."""

import hashlib
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class CommandConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    root: str = "/"
    python: str = Field(default="python3", description="Guest Python executable name on its PATH, or absolute path.")
    shell: str = "/bin/bash"
    read_only: bool = False
    request_timeout_seconds: float = Field(default=120, gt=0, le=600, allow_inf_nan=False)
    max_file_bytes: int = Field(default=16 * 1024 * 1024, gt=0, le=64 * 1024 * 1024)
    max_query_entries: int = Field(default=10_000, gt=0, le=100_000)
    max_output_bytes: int = Field(default=1024 * 1024, gt=0, le=16 * 1024 * 1024)

    @field_validator("root", "shell")
    @classmethod
    def _absolute_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not value.startswith("/") or value.startswith("//") or "\x00" in value or ".." in path.parts:
            raise ValueError("Paths must be absolute without traversal")
        return str(path)

    @field_validator("python")
    @classmethod
    def _python_executable(cls, value: str) -> str:
        if "/" in value:
            return cls._absolute_path(value)
        if (
            not value
            or not all(character.isalnum() or character in "._-" for character in value)
            or value in {".", ".."}
        ):
            raise ValueError("Python must be a guest executable name or absolute path")
        return value

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class TokenCredential(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    api_key: SecretStr = Field(min_length=1, title="API key")


class TargetState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    target_id: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.:-]+$")
    environment_id: str = Field(min_length=1, max_length=128)
    configuration_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class NamedTargetState(TargetState):
    backing_id: str = Field(min_length=1, max_length=256)
