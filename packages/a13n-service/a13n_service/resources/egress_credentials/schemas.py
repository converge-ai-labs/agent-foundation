"""Write-only credential inputs and nonsecret resource views."""

import ipaddress
import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, SecretStr, StringConstraints, field_validator

from a13n_service.infra.ids import ObjectId


def hostname(value: str) -> str:
    """Envd injection accepts exact ASCII DNS names, never IPs, URLs or patterns."""
    if not value.isascii():
        raise ValueError("Expected an ASCII DNS hostname")
    value = value.removesuffix(".").lower()
    if not 1 <= len(value) <= 253 or not all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in value.split(".")
    ):
        raise ValueError("Expected an exact DNS hostname")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise ValueError("Credential injection requires a DNS hostname, not an IP address")


def secret_value(value: SecretStr) -> SecretStr:
    raw = value.get_secret_value()
    if not 1 <= len(raw.encode()) <= 8192 or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise ValueError("Credential must contain 1 to 8192 UTF-8 bytes without control characters")
    return value


type Hosts = Annotated[
    tuple[Annotated[str, AfterValidator(hostname)], ...],
    Field(min_length=1, max_length=256),
    AfterValidator(lambda values: tuple(sorted(set(values)))),
]
type CredentialValue = Annotated[SecretStr, AfterValidator(secret_value)]
type Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
type Description = Annotated[str, StringConstraints(max_length=2048)]


class SecretReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=128)
    credential_id: ObjectId
    inject_hosts: Hosts

    @field_validator("env")
    @classmethod
    def unreserved_name(cls, value: str) -> str:
        if value.upper().startswith(("A13N_ENVD_", "EIP_")) or value in {
            "PATH",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE",
            "NODE_EXTRA_CA_CERTS",
        }:
            raise ValueError("Credential variables cannot replace Envd transport or trust configuration")
        return value


class CredentialCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    description: Description = ""
    allowed_hosts: Hosts
    value: CredentialValue = Field(repr=False)


class CredentialUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    description: Description | None = None


class CredentialRotation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: CredentialValue = Field(repr=False)


class Credential(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    workspace_id: str
    name: str
    description: str
    allowed_hosts: tuple[str, ...]
    state: Literal["active", "revoked"]
    credential_version: int
    version: int
    created_by_id: str
    updated_by_id: str
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None


class CredentialPage(BaseModel):
    items: list[Credential]
    next_cursor: str | None
