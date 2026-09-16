"""Public and durable values owned by Model Management."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Annotated, Literal

from a13n_harness import ModelCapability
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_ai.settings import ThinkingEffort

from a13n_service.iam.domain import PrincipalRef
from a13n_service.ids import ObjectId, new_object_id
from a13n_service.names import DisplayName

from .headers import HeaderUpdates

BoundedDescription = Annotated[str, StringConstraints(max_length=2048)]
UpstreamModel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
BaseModelName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
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


def new_model_provider_id() -> str:
    return new_object_id("mprov")


def new_model_id() -> str:
    return new_object_id("mdl")


class ModelProfile(BaseModel):
    """Read-only Provider capability information returned by discovery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_modalities: tuple[Literal["text", "image", "audio", "video"], ...] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    supports_tools: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    supports_json_schema_output: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    supports_json_object_output: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    supports_image_output: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    supports_audio_input: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    supports_thinking: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    thinking_always_enabled: bool | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def normalize_modalities(self) -> ModelProfile:
        if self.input_modalities is not None:
            object.__setattr__(self, "input_modalities", tuple(dict.fromkeys(self.input_modalities)))
        return self


class ModelLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    context_window_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)


class ModelPricing(BaseModel):
    """Editable USD prices per million tokens."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    output: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cache_read: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cache_write: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ModelDeclarations(BaseModel):
    """Harness-facing facts and authoring choices declared for one saved Model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    supports_tools: bool | None = None
    thinking_efforts: tuple[ThinkingEffort, ...] = ()
    capabilities: frozenset[ModelCapability] = Field(default_factory=frozenset)
    context_window_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    structured_output: bool | None = None
    pricing: ModelPricing | None = None

    @field_validator("thinking_efforts")
    @classmethod
    def validate_unique_efforts(cls, value: tuple[ThinkingEffort, ...]) -> tuple[ThinkingEffort, ...]:
        if len(value) != len(set(value)):
            raise ValueError("thinking efforts must be unique")
        return value


class CreateModelProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ProviderType
    name: DisplayName
    configuration: dict[str, object] = Field(default_factory=dict)
    credential: SecretStr | None = Field(default=None, json_schema_extra={"writeOnly": True})
    extra_headers: HeaderUpdates = Field(default_factory=dict)
    enabled: bool = True


class UpdateModelProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: DisplayName | None = None
    configuration: dict[str, object] | None = None
    credential: SecretStr | None = Field(default=None, json_schema_extra={"writeOnly": True})
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
    base_model: BaseModelName | None = None
    model_api: ModelApi | None = None
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    declarations: ModelDeclarations = Field(default_factory=ModelDeclarations)
    enabled: bool = True


class UpdateModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: DisplayName | None = None
    description: BoundedDescription | None = None
    upstream_model: UpstreamModel | None = None
    base_model: BaseModelName | None = None
    model_api: ModelApi | None = None
    settings: dict[str, JsonValue] | None = None
    declarations: ModelDeclarations | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def validate_change(self) -> UpdateModelRequest:
        if not self.model_fields_set:
            raise ValueError("at least one field must be supplied")
        for field in self.model_fields_set - {"description", "base_model"}:
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
    base_model: str | None = None
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
    base_model: str | None = None
    pricing: ModelPricing | None = None

    @classmethod
    def freeze(cls, model: Model) -> ModelExecutionSnapshot:
        return cls(
            model_id=model.id,
            model_key=model.key,
            upstream_model=model.upstream_model,
            base_model=model.base_model,
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


class ModelCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    upstream_model: UpstreamModel
    display_name: DisplayName | None = None
    suggested_model_api: str
    suggested_settings: dict[str, JsonValue] = Field(default_factory=dict)
    profile: ModelProfile = Field(default_factory=ModelProfile)
    native_profile: ModelProfile = Field(default_factory=ModelProfile)
    limits: ModelLimits = Field(default_factory=ModelLimits)
    parameter_support: dict[str, Literal["supported", "unsupported", "unknown"]] = Field(default_factory=dict)


class ModelDiscovery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ModelCandidate, ...]


class ModelCatalogSuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: ObjectId
    upstream_model: UpstreamModel
    base_model: BaseModelName | None = None
    model_api: ModelApi | None = None


class BaseModelCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_model: BaseModelName
    inferred_model_api: ModelApi | None
    model_api_label: str | None


class BaseModelCandidateCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[BaseModelCandidate, ...]


class ModelCatalogSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_model: BaseModelName
    model_api: ModelApi | None
    model_api_label: str | None
    declarations: ModelDeclarations


class ModelCatalogMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["none", "explicit", "exact", "normalized", "name_tokens", "ambiguous"]
    items: tuple[ModelCatalogSuggestion, ...] = ()
