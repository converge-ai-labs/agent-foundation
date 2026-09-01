"""Public and durable values owned by Foundation Environment Management."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from a13n_environment_provider import EnvironmentProviderSpec
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from a13n_service.iam.domain import ObjectId, PrincipalRef
from a13n_service.ids import new_object_id
from a13n_service.secrets.domain import SecretCredentialSource

EnvironmentName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
EnvironmentDescription = Annotated[str, StringConstraints(max_length=4096)]
ProviderKey = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$", min_length=3, max_length=128),
]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
BoundedKey = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$", min_length=1, max_length=128),
]


def new_environment_id() -> str:
    return new_object_id("env")


def new_environment_revision_id() -> str:
    return new_object_id("envr")


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EnvironmentAccess(StrEnum):
    read_only = "read_only"
    read_write = "read_write"
    full = "full"


class EnvironmentProviderLock(DomainModel):
    """Exact process-catalog provenance without exposing an import target."""

    schema_version: Literal["1"] = "1"
    provider_key: ProviderKey
    distribution_name: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = None
    distribution_version: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = None
    builtin: bool
    registration_digest_sha256: Sha256Digest


class EnvironmentProviderCatalogEntry(DomainModel):
    provider_key: ProviderKey
    configuration_versions: tuple[str, ...] = Field(min_length=1, max_length=64)
    provider_lock: EnvironmentProviderLock


class EnvironmentProviderCatalogEntryCollection(DomainModel):
    items: tuple[EnvironmentProviderCatalogEntry, ...]


class EnvironmentProviderSelection(DomainModel):
    organization_id: ObjectId
    workspace_id: ObjectId
    provider_key: ProviderKey
    provider_package_revision_id: ObjectId | None = None
    provider_lock: EnvironmentProviderLock
    enabled: bool
    version: int = Field(ge=1)
    updated_by: PrincipalRef
    updated_at: datetime


class EnvironmentCredentialBinding(DomainModel):
    requirement_key: BoundedKey
    credential: SecretCredentialSource


class Environment(DomainModel):
    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId
    name: EnvironmentName
    description: str | None
    version: int = Field(ge=1)
    current_revision_id: ObjectId
    archived_at: datetime | None
    created_by: PrincipalRef
    updated_by: PrincipalRef
    created_at: datetime
    updated_at: datetime


class EnvironmentRevision(DomainModel):
    id: ObjectId
    environment_id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId
    revision_number: int = Field(ge=1)
    provider: EnvironmentProviderSpec
    provider_package_revision_id: ObjectId | None = None
    provider_lock: EnvironmentProviderLock
    credential_bindings: tuple[EnvironmentCredentialBinding, ...] = Field(default=(), max_length=64)
    access: EnvironmentAccess = EnvironmentAccess.full
    logical_digest_sha256: Sha256Digest
    created_by: PrincipalRef
    created_at: datetime


class EnvironmentCollection(DomainModel):
    items: tuple[Environment, ...]
    next_cursor: str | None


class EnvironmentRevisionCollection(DomainModel):
    items: tuple[EnvironmentRevision, ...]
    next_cursor: str | None


class PutEnvironmentProviderSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    expected_version: int | None = Field(default=None, ge=1)


class CreateEnvironmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: EnvironmentName
    description: EnvironmentDescription | None = None
    provider: EnvironmentProviderSpec
    credential_bindings: tuple[EnvironmentCredentialBinding, ...] = Field(default=(), max_length=64)
    access: EnvironmentAccess = EnvironmentAccess.full

    @model_validator(mode="after")
    def validate_bindings(self) -> CreateEnvironmentRequest:
        return _require_unique_bindings(self)


class PatchEnvironmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    name: EnvironmentName | None = None
    description: EnvironmentDescription | None = None
    archived: bool | None = None

    @model_validator(mode="after")
    def validate_change(self) -> PatchEnvironmentRequest:
        changed = self.model_fields_set.intersection({"name", "description", "archived"})
        if not changed:
            raise ValueError("at least one mutable field must be supplied")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        if "archived" in self.model_fields_set and self.archived is None:
            raise ValueError("archived cannot be null")
        return self


class CreateEnvironmentRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_environment_version: int = Field(ge=1)
    provider: EnvironmentProviderSpec
    credential_bindings: tuple[EnvironmentCredentialBinding, ...] = Field(default=(), max_length=64)
    access: EnvironmentAccess = EnvironmentAccess.full

    @model_validator(mode="after")
    def validate_bindings(self) -> CreateEnvironmentRevisionRequest:
        return _require_unique_bindings(self)


class EnvironmentRevisionTestResult(DomainModel):
    success: Literal[True] = True
    code: Literal["configuration_valid"] = "configuration_valid"


def environment_logical_digest(
    *,
    provider: EnvironmentProviderSpec,
    provider_package_revision_id: str | None,
    provider_lock: EnvironmentProviderLock,
    credential_bindings: tuple[EnvironmentCredentialBinding, ...],
    access: EnvironmentAccess,
) -> str:
    payload = {
        "schema_version": "1",
        "provider": provider.model_dump(mode="json"),
        "provider_package_revision_id": provider_package_revision_id,
        "provider_lock": provider_lock.model_dump(mode="json"),
        "credential_bindings": [item.model_dump(mode="json") for item in credential_bindings],
        "access": access.value,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _require_unique_bindings[RequestT: CreateEnvironmentRequest | CreateEnvironmentRevisionRequest](
    request: RequestT,
) -> RequestT:
    keys = tuple(item.requirement_key for item in request.credential_bindings)
    if len(keys) != len(set(keys)):
        raise ValueError("credential requirement keys must be unique")
    return request
