"""Credential-free E2B recipes and reconnect state."""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

PROVIDER_KEY = "a13n.e2b"


class E2BBackendConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: str = Field(default="e2b.dev", pattern=r"^[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?$")


class E2BCredential(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: SecretStr = Field(min_length=1)


class E2BProviderConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    template: str = Field(default="base", min_length=1, max_length=256)
    root: str = "/home/user"
    user: str = Field(default="user", pattern=r"^[a-z_][a-z0-9_-]{0,63}$")
    python: str = "/usr/bin/python3"
    shell: str = "/bin/bash"
    timeout_seconds: int = Field(default=300, ge=30, le=86_400)
    request_timeout_seconds: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    allow_internet_access: bool = True
    read_only: bool = False
    max_file_bytes: int = Field(default=16 * 1024 * 1024, gt=0, le=1024 * 1024 * 1024)
    max_output_bytes: int = Field(default=8 * 1024 * 1024, gt=0, le=1024 * 1024 * 1024)
    max_processes: int = Field(default=16, gt=0, le=256)
    max_wall_time_seconds: float = Field(default=300, gt=0, le=86_400, allow_inf_nan=False)
    max_query_entries: int = Field(default=10_000, gt=0, le=100_000)

    @field_validator("root", "python", "shell")
    @classmethod
    def _absolute_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not value.startswith("/") or "\x00" in value or ".." in path.parts or value.startswith("//"):
            raise ValueError("E2B paths must be absolute without traversal")
        return str(path)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class E2BProviderStateData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sandbox_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    environment_id: str = Field(min_length=1, max_length=128)
    configuration_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
