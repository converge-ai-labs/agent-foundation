"""Credential-free E2B recipes and reconnect state."""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

PROVIDER_KEY = "a13n.e2b"


class E2BBackendConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: str = Field(
        default="e2b.dev",
        title="Sandbox Domain",
        description="Sandbox domain without https:// or a path. Use the domain supplied by your sandbox service.",
        pattern=r"^[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?$",
    )
    api_url: str | None = Field(
        default=None,
        title="API URL",
        json_schema_extra={"x-placeholder": "https://api.e2b.dev"},
        description="Optional control API endpoint, including https://. Leave empty to use https://api.<domain>.",
    )

    @field_validator("api_url")
    @classmethod
    def _api_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("API URL must be an HTTP(S) URL without credentials, query, or fragment")
        _ = url.port
        return value


class E2BCredential(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: SecretStr = Field(min_length=1, title="API Key")


class E2BProviderConfiguration(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        json_schema_extra={"x-primary-fields": ["template", "timeout_seconds", "allow_internet_access"]},
    )

    template: str = Field(
        default="base",
        min_length=1,
        max_length=256,
        title="E2B template name or ID",
        description="Use an existing E2B template. Preinstalled software, CPU and memory are configured when building that template in E2B.",
    )
    root: str = "/home/user"
    user: str = Field(default="user", pattern=r"^[a-z_][a-z0-9_-]{0,63}$")
    python: str = "/usr/bin/python3"
    timeout_seconds: int = Field(default=3600, ge=30, le=86_400, title="Sandbox timeout (seconds)")
    request_timeout_seconds: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    allow_internet_access: bool = Field(default=True, title="Allow internet access")
    read_only: bool = False
    max_file_bytes: int = Field(default=16 * 1024 * 1024, gt=0, le=1024 * 1024 * 1024)
    max_observation_bytes: int = Field(default=1024 * 1024, gt=0, le=16 * 1024 * 1024)
    max_active_observations: int = Field(default=128, gt=0, le=1024)
    max_retained_output_bytes: int = Field(default=128 * 1024 * 1024, gt=0, le=1024 * 1024 * 1024)
    max_query_entries: int = Field(default=10_000, gt=0, le=100_000)

    @field_validator("root", "python")
    @classmethod
    def _absolute_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not value.startswith("/") or "\x00" in value or ".." in path.parts or value.startswith("//"):
            raise ValueError("E2B paths must be absolute without traversal")
        return str(path)

    @property
    def fingerprint(self) -> str:
        target = self.model_dump_json(include={"template", "root", "user", "allow_internet_access"})
        return hashlib.sha256(target.encode()).hexdigest()


class E2BProviderStateData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sandbox_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    environment_id: str = Field(min_length=1, max_length=128)
    configuration_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
