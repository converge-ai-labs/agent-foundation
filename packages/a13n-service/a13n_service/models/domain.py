"""Public and durable values owned by Model Management."""

from __future__ import annotations

import unicodedata
from datetime import date, datetime
from typing import Annotated, Literal

from a13n_harness import ModelCapability
from a13n_harness.token_pricing import TokenPricing
from a13n_harness.toolsets.file_media import NativeInputMediaKind
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

from a13n_service.iam.domain import PrincipalRef
from a13n_service.ids import ObjectId, new_object_id
from a13n_service.names import DisplayName

from .headers import HeaderUpdates

BoundedDescription = Annotated[str, StringConstraints(max_length=2048)]
UpstreamModel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
ProviderType = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
ModelApi = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$", max_length=96)]


def normalize_key(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).strip().casefold()
    if not 1 <= len(normalized) <= 128:
        raise ValueError("key must contain between 1 and 128 characters")
    if not normalized[0].isalnum() or not normalized[-1].isalnum():
        raise ValueError("key must begin and end with a letter or number")
    if any(not (character.isalnum() or character in "._-/") for character in normalized):
        raise ValueError("key may contain only letters, numbers, '.', '_', '-', and '/'")
    return normalized


ModelKey = Annotated[str, AfterValidator(normalize_key)]

MEDIA_KINDS: tuple[NativeInputMediaKind, ...] = ("image", "video", "audio")


class MediaUnderstandingSelection(BaseModel):
    """Per-kind auxiliary Model choice shared by Workspace, Agent, and Run levels."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    image: ModelKey | None = None
    video: ModelKey | None = None
    audio: ModelKey | None = None

    def selections(self) -> dict[NativeInputMediaKind, str]:
        return {kind: key for kind in MEDIA_KINDS if (key := getattr(self, kind)) is not None}


def new_model_provider_id() -> str:
    return new_object_id("mprov")


def new_model_id() -> str:
    return new_object_id("mdl")


class CatalogRef(BaseModel):
    """A models.dev provider-qualified identity, independent of the outbound ID."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")]
    model: UpstreamModel


class ModelDeclarations(BaseModel):
    """Harness-facing facts and authoring choices declared for one saved Model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    supports_tools: bool | None = None
    capabilities: frozenset[ModelCapability] = Field(default_factory=frozenset)
    context_window_tokens: int | None = Field(default=None, gt=0)
    structured_output: bool | None = None
    pricing: TokenPricing | None = None


class CreateModelProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ProviderType
    name: DisplayName
    configuration: dict[str, object] = Field(default_factory=dict)
    credential: dict[str, object] | None = Field(default=None, repr=False, json_schema_extra={"writeOnly": True})
    extra_headers: HeaderUpdates = Field(default_factory=dict)
    enabled: bool = True


class UpdateModelProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: DisplayName | None = None
    configuration: dict[str, object] | None = None
    credential: dict[str, object] | None = Field(default=None, repr=False, json_schema_extra={"writeOnly": True})
    extra_headers: HeaderUpdates = Field(default_factory=dict)
    enabled: bool | None = None

    @model_validator(mode="after")
    def validate_change(self) -> UpdateModelProviderRequest:
        if not self.model_fields_set:
            raise ValueError("at least one field must be supplied")
        for field in self.model_fields_set - {"credential"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class ModelProvider(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId | None
    type: str
    name: str
    configuration: dict[str, object]
    credential_configured: bool
    header_names: tuple[str, ...] = ()
    enabled: bool
    created_by: PrincipalRef
    updated_by: PrincipalRef
    created_at: datetime
    updated_at: datetime


class ModelProviderCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ModelProvider, ...]
    next_cursor: str | None


class CreateModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: ModelKey
    provider_id: ObjectId
    name: DisplayName
    description: BoundedDescription | None = None
    upstream_model: UpstreamModel
    catalog_ref: CatalogRef | None = None
    model_api: ModelApi
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    declarations: ModelDeclarations = Field(default_factory=ModelDeclarations)
    enabled: bool = True


class UpdateModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: DisplayName | None = None
    description: BoundedDescription | None = None
    upstream_model: UpstreamModel | None = None
    catalog_ref: CatalogRef | None = None
    model_api: ModelApi | None = None
    settings: dict[str, JsonValue] | None = None
    declarations: ModelDeclarations | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def validate_change(self) -> UpdateModelRequest:
        if not self.model_fields_set:
            raise ValueError("at least one field must be supplied")
        for field in self.model_fields_set - {"description", "catalog_ref"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId | None
    key: str
    provider_id: ObjectId
    name: str
    description: str | None
    upstream_model: str
    model_api: ModelApi
    catalog_ref: CatalogRef | None = None
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    declarations: ModelDeclarations = Field(default_factory=ModelDeclarations)
    enabled: bool
    created_by: PrincipalRef
    updated_by: PrincipalRef
    created_at: datetime
    updated_at: datetime


class ModelCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[Model, ...]
    next_cursor: str | None


class ModelTestRequest(BaseModel):
    """Model tests use the saved API and settings without a request selector."""

    model_config = ConfigDict(extra="forbid", frozen=True)


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
    model_key: str
    upstream_model: str
    model_api: str


class ModelExecutionSnapshot(BaseModel):
    """Model fields frozen at Run acceptance; Provider configuration stays live."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    model_id: ObjectId
    model_key: str
    upstream_model: str
    model_api: str
    catalog_ref: CatalogRef | None = None
    pricing: TokenPricing | None = None

    @classmethod
    def freeze(cls, model: Model) -> ModelExecutionSnapshot:
        return cls(
            model_id=model.id,
            model_key=model.key,
            upstream_model=model.upstream_model,
            catalog_ref=model.catalog_ref,
            model_api=model.model_api,
            pricing=model.declarations.pricing,
        )

    def observation(self) -> ModelExecutionObservation:
        return ModelExecutionObservation(
            model_id=self.model_id,
            model_key=self.model_key,
            upstream_model=self.upstream_model,
            model_api=self.model_api,
        )


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: CatalogRef
    identity: str
    name: str
    provider_name: str
    release_date: date
    declarations: ModelDeclarations
    pricing_warning: str | None = None


class ModelCatalogCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[CatalogModel, ...] = ()
    status: Literal["ready", "stale", "unavailable"]
    released_since: date
