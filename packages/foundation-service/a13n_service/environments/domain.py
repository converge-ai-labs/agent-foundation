"""Public and durable values owned by Foundation Environment Management."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

import rfc8785
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

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
SchemaVersion = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", min_length=1, max_length=64),
]
TargetKey = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
JsonObject = dict[str, JsonValue]
BoundedKey = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$", min_length=1, max_length=128),
]

_JSON_OBJECT_ADAPTER = TypeAdapter(JsonObject)
_MAX_CONNECTION_PARAMETERS_BYTES = 256 * 1024


def new_environment_id() -> str:
    return new_object_id("env")


def new_environment_revision_id() -> str:
    return new_object_id("envr")


def new_run_environment_binding_id() -> str:
    return new_object_id("envb")


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
    connection_versions: tuple[SchemaVersion, ...] = Field(min_length=1, max_length=64)
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
    updated_by: PrincipalRef
    updated_at: datetime


class EnvironmentCredentialBinding(DomainModel):
    requirement_key: BoundedKey
    credential: SecretCredentialSource


class EnvironmentConnectionSpec(DomainModel):
    provider_key: ProviderKey
    schema_version: SchemaVersion
    parameters: JsonObject

    @field_validator("parameters", mode="before")
    @classmethod
    def validate_parameters(cls, value: object) -> JsonObject:
        parameters = _JSON_OBJECT_ADAPTER.validate_python(value)
        try:
            encoded = rfc8785.dumps(parameters)
        except rfc8785.CanonicalizationError as error:
            raise ValueError("connection parameters must be finite JSON") from error
        if len(encoded) > _MAX_CONNECTION_PARAMETERS_BYTES:
            raise ValueError("connection parameters exceed the size limit")
        return _JSON_OBJECT_ADAPTER.validate_json(encoded)


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
    version: int = Field(ge=1)
    connection: EnvironmentConnectionSpec
    provider_package_revision_id: ObjectId | None = None
    provider_lock: EnvironmentProviderLock
    credential_bindings: tuple[EnvironmentCredentialBinding, ...] = Field(default=(), max_length=64)
    access: EnvironmentAccess = EnvironmentAccess.full
    target_key: TargetKey
    logical_digest_sha256: Sha256Digest
    created_by: PrincipalRef
    created_at: datetime

    def summary(self) -> EnvironmentRevisionSummary:
        return EnvironmentRevisionSummary(
            id=self.id,
            environment_id=self.environment_id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            version=self.version,
            provider_key=self.connection.provider_key,
            provider_package_revision_id=self.provider_package_revision_id,
            provider_lock=self.provider_lock,
            access=self.access,
            logical_digest_sha256=self.logical_digest_sha256,
            created_by=self.created_by,
            created_at=self.created_at,
        )


class EnvironmentRevisionSummary(DomainModel):
    """Collection-safe Revision projection without connection or target details."""

    id: ObjectId
    environment_id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId
    version: int = Field(ge=1)
    provider_key: ProviderKey
    provider_package_revision_id: ObjectId | None = None
    provider_lock: EnvironmentProviderLock
    access: EnvironmentAccess
    logical_digest_sha256: Sha256Digest
    created_by: PrincipalRef
    created_at: datetime


class RunEnvironmentBinding(DomainModel):
    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId
    run_id: ObjectId
    mount_name: Literal["workspace"] = "workspace"
    source_environment_revision_id: ObjectId | None = None
    provider_key: ProviderKey
    target_key: TargetKey
    environment_execution_config_digest_sha256: Sha256Digest
    created_at: datetime


class EnvironmentCollection(DomainModel):
    items: tuple[Environment, ...]
    next_cursor: str | None


class EnvironmentRevisionCollection(DomainModel):
    items: tuple[EnvironmentRevisionSummary, ...]
    next_cursor: str | None


class PutEnvironmentProviderSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class CreateEnvironmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: EnvironmentName
    description: EnvironmentDescription | None = None
    connection: EnvironmentConnectionSpec
    credential_bindings: tuple[EnvironmentCredentialBinding, ...] = Field(default=(), max_length=64)
    access: EnvironmentAccess = EnvironmentAccess.full

    @model_validator(mode="after")
    def validate_bindings(self) -> CreateEnvironmentRequest:
        return _require_unique_bindings(self)


class UpdateEnvironmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: EnvironmentName | None = None
    description: EnvironmentDescription | None = None
    archived: bool | None = None

    @model_validator(mode="after")
    def validate_change(self) -> UpdateEnvironmentRequest:
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

    expected_version: int = Field(ge=1)
    connection: EnvironmentConnectionSpec
    credential_bindings: tuple[EnvironmentCredentialBinding, ...] = Field(default=(), max_length=64)
    access: EnvironmentAccess = EnvironmentAccess.full

    @model_validator(mode="after")
    def validate_bindings(self) -> CreateEnvironmentRevisionRequest:
        return _require_unique_bindings(self)


class EnvironmentRevisionTestResult(DomainModel):
    success: Literal[True] = True
    code: Literal["attachment_ready"] = "attachment_ready"


def environment_logical_digest(
    *,
    connection: EnvironmentConnectionSpec,
    provider_package_revision_id: str | None,
    provider_lock: EnvironmentProviderLock,
    credential_bindings: tuple[EnvironmentCredentialBinding, ...],
    access: EnvironmentAccess,
    target_key: str,
) -> str:
    payload = {
        "schema_version": "1",
        "connection": connection.model_dump(mode="json"),
        "provider_package_revision_id": provider_package_revision_id,
        "provider_lock": provider_lock.model_dump(mode="json"),
        "credential_bindings": [item.model_dump(mode="json") for item in credential_bindings],
        "access": access.value,
        "target_key": target_key,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def validated_target_key(value: str) -> str:
    if value != value.strip() or any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
        raise ValueError("target key must be trimmed and contain no control or surrogate characters")
    return TypeAdapter(TargetKey).validate_python(value)


def _require_unique_bindings[RequestT: CreateEnvironmentRequest | CreateEnvironmentRevisionRequest](
    request: RequestT,
) -> RequestT:
    keys = tuple(item.requirement_key for item in request.credential_bindings)
    if len(keys) != len(set(keys)):
        raise ValueError("credential requirement keys must be unique")
    return request
