"""Public and durable values owned by Model Management."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

from a13n_service.iam.domain import PrincipalRef
from a13n_service.ids import new_object_id
from a13n_service.secrets.domain import InvokingUserSecretCredential, WorkspaceSecretCredential

BoundedName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
BoundedDescription = Annotated[str, StringConstraints(max_length=2048)]
BoundedModelName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
ObjectId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]{1,7}_[a-z0-9]{16,64}$")]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ProviderType = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]


def new_model_id() -> str:
    """Allocate one unpredictable, kind-prefixed Model identifier."""

    return new_object_id("mdl")


def new_model_revision_id() -> str:
    """Allocate one unpredictable, kind-prefixed ModelRevision identifier."""

    return new_object_id("mdlr")


class NoCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["none"] = "none"


ModelCredential = Annotated[
    WorkspaceSecretCredential | InvokingUserSecretCredential | NoCredential,
    Field(discriminator="source"),
]


class ModelCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_modalities: tuple[str, ...] = Field(default=(), max_length=16)
    context_window_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    tool_calling: bool | None = None
    structured_output: bool | None = None
    reasoning: bool | None = None

    @model_validator(mode="after")
    def validate_modalities(self) -> ModelCapabilities:
        normalized = tuple(dict.fromkeys(item.strip().lower() for item in self.input_modalities if item.strip()))
        if any(len(item) > 32 or not item.replace("_", "").isalnum() for item in normalized):
            raise ValueError("input_modalities contains an invalid value")
        object.__setattr__(self, "input_modalities", normalized)
        return self


class CapabilitySource(StrEnum):
    catalog = "catalog"
    manual_override = "manual_override"


class ModelRevisionInput(BaseModel):
    """Provider configuration accepted before provider-specific normalization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_type: ProviderType
    model_name: BoundedModelName
    credential: ModelCredential
    provider_config: dict[str, object] = Field(default_factory=dict)
    capabilities: ModelCapabilities | None = None


class CreateModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: BoundedName
    description: BoundedDescription | None = None
    enabled: bool = True
    config: ModelRevisionInput


class UpdateModelRequest(BaseModel):
    """Mutable Model metadata; concurrency is carried by HTTP If-Match."""

    model_config = ConfigDict(extra="forbid")

    name: BoundedName | None = None
    description: BoundedDescription | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def validate_change(self) -> UpdateModelRequest:
        if not self.model_fields_set:
            raise ValueError("at least one metadata field must be supplied")
        invalid = sorted(
            field for field in self.model_fields_set if field != "description" and getattr(self, field) is None
        )
        if invalid:
            raise ValueError(f"fields cannot be null: {', '.join(invalid)}")
        return self


class CreateModelRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    config: ModelRevisionInput


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId
    name: str
    description: str | None
    version: int = Field(ge=1)
    current_revision_id: ObjectId
    enabled: bool
    created_by: PrincipalRef
    updated_by: PrincipalRef
    created_at: datetime
    updated_at: datetime


class ModelRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId
    model_id: ObjectId
    version: int = Field(ge=1)
    provider_type: str
    model_name: str
    base_url: str | None
    credential: ModelCredential
    provider_config: dict[str, object]
    capabilities: ModelCapabilities
    capability_source: CapabilitySource
    content_digest: Sha256Digest
    created_by: PrincipalRef
    created_at: datetime


class ModelRevisionCreateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: Model
    revision: ModelRevision


class ModelCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[Model, ...]
    next_cursor: str | None


class ModelRevisionCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ModelRevision, ...]


class ModelConnectionTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    success: bool
    elapsed_ms: int = Field(ge=0)
    code: str
    message: str
    may_consume_quota_or_incur_cost: Literal[True] = True


class ModelExecutionObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: ObjectId
    model_revision_id: ObjectId
    provider_type: str
    model_name: str


class ModelExecutionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    model_id: ObjectId
    model_revision_id: ObjectId
    provider_type: str
    model_name: str
    base_url: str | None
    credential: ModelCredential
    provider_config: dict[str, object]
    adapter_key: str
    adapter_version: str

    @classmethod
    def freeze(
        cls,
        revision: ModelRevision,
        *,
        adapter_key: str,
        adapter_version: str,
    ) -> ModelExecutionSnapshot:
        return cls(
            model_id=revision.model_id,
            model_revision_id=revision.id,
            provider_type=revision.provider_type,
            model_name=revision.model_name,
            base_url=revision.base_url,
            credential=revision.credential,
            provider_config=revision.provider_config,
            adapter_key=adapter_key,
            adapter_version=adapter_version,
        )

    def observation(self) -> ModelExecutionObservation:
        return ModelExecutionObservation(
            model_id=self.model_id,
            model_revision_id=self.model_revision_id,
            provider_type=self.provider_type,
            model_name=self.model_name,
        )


def model_revision_digest(
    *,
    provider_type: str,
    model_name: str,
    base_url: str | None,
    credential: ModelCredential,
    provider_config: dict[str, object],
    capabilities: ModelCapabilities,
    capability_source: CapabilitySource,
) -> str:
    payload = {
        "provider_type": provider_type,
        "model_name": model_name,
        "base_url": base_url,
        "credential": TypeAdapter(ModelCredential).dump_python(credential, mode="json"),
        "provider_config": provider_config,
        "capabilities": capabilities.model_dump(mode="json"),
        "capability_source": capability_source.value,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()
