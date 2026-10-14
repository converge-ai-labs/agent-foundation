"""Explicit routing for Docker-managed HTTP EIP endpoints."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DockerHttpConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = Field(
        title="Host access address",
        min_length=1,
        max_length=253,
        description="Host name or IP reachable by Service/Worker, without a scheme or port. Ports are assigned automatically.",
    )
    bind_host: str | None = Field(
        default=None,
        title="Listen address",
        description="Leave empty to use the host IP (localhost uses 127.0.0.1). Other hostnames require an explicit Engine host IP.",
    )
    allow_plaintext_private_link: bool = Field(
        default=False,
        title="Allow trusted private HTTP",
        description="Explicitly trust an unencrypted private route. Never expose this endpoint on the public Internet.",
    )
    credential_file: str = Field(
        default="/var/lib/a13n-envd/http-token",
        title="Credential file",
        max_length=4096,
        description="Image-owned, protected per-container HTTP credential file.",
    )

    @field_validator("host")
    @classmethod
    def valid_host(cls, value: str) -> str:
        parsed = urlsplit(f"http://{value}:8888")
        if (
            not parsed.hostname
            or parsed.port != 8888
            or parsed.username is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or any(c.isspace() for c in value)
        ):
            raise ValueError("Use a hostname or bracketed IPv6 address without scheme, port or path")
        return value

    @field_validator("bind_host")
    @classmethod
    def valid_bind(cls, value: str | None) -> str | None:
        return str(ipaddress.ip_address(value)) if value else None

    @property
    def listen_address(self) -> str:
        if self.bind_host is not None:
            return self.bind_host
        host = urlsplit(f"http://{self.host}:8888").hostname
        if host == "localhost":
            return "127.0.0.1"
        try:
            return str(ipaddress.ip_address(host or ""))
        except ValueError as exc:
            raise ValueError("Hostnames require a listen address in advanced settings; use an Engine host IP") from exc

    @field_validator("credential_file")
    @classmethod
    def valid_path(cls, value: str) -> str:
        from pathlib import PurePosixPath

        if not value.startswith("/") or ".." in PurePosixPath(value).parts or "\x00" in value:
            raise ValueError("Credential file must be an absolute normalized path")
        return value

    @model_validator(mode="after")
    def validate_transport(self) -> DockerHttpConfiguration:
        from a13n_harness.providers.environment.remote_envd.configuration import HttpEnvdConnectionConfiguration

        if not ipaddress.ip_address(self.listen_address).is_loopback and not self.allow_plaintext_private_link:
            raise ValueError("Non-loopback port publication requires explicit plaintext private-link consent")
        HttpEnvdConnectionConfiguration(
            endpoint=f"http://{self.host}:8888", allow_plaintext_private_link=self.allow_plaintext_private_link
        )
        return self
