"""Models as the API accepts and returns them; `ModelConfig` is what execution reads."""

from datetime import date, datetime
from typing import Literal

from a13n_harness.pricing import ModelPricingEntry
from a13n_harness.providers.model.headers import ExtraHeaders
from a13n_harness.spec import HarnessModelCharacteristics
from a13n_harness.toolsets.file_media import NativeInputMediaKind
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from a13n_service.infra.ids import Key, ObjectId
from a13n_service.providers.model_settings import JsonSettings


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # The upstream model name the provider serves.
    model_name: str = Field(min_length=1, max_length=256)
    # One of the provider type's calling APIs, listed by `GET /provider-types/model`.
    model_api: str = Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$", max_length=96)
    # Context window and modalities.
    characteristics: HarnessModelCharacteristics = Field(default_factory=HarnessModelCharacteristics)
    max_tokens: int | None = Field(default=None, ge=1, le=1000000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    extra_body: JsonSettings = Field(default_factory=dict)
    extra_headers: ExtraHeaders = Field(default_factory=dict)

    def defaults(self) -> dict[str, JsonValue]:
        """Only configured defaults; APIs without raw-body support do not receive an empty placeholder."""
        values: dict[str, JsonValue] = {
            name: value
            for name, value in (
                ("max_tokens", self.max_tokens),
                ("temperature", self.temperature),
                ("top_p", self.top_p),
            )
            if value is not None
        }
        if self.extra_body:
            values["extra_body"] = dict(self.extra_body)
        if self.extra_headers:
            values["extra_headers"] = dict(self.extra_headers)
        return values


class CatalogRef(BaseModel):
    """A models.dev channel and the model ID it lists there."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")
    model: str = Field(min_length=1, max_length=256)


class ModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: ObjectId
    # What agents select the model by; `{provider type}-{upstream name}` with other characters as `-` when omitted.
    key: Key | None = None
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2048)
    config: ModelConfig
    # Prices this model's own calls; the provider and model the entry names only record where it came from.
    pricing: ModelPricingEntry | None = None
    # The catalog model the caller started from; recorded as given, never resolved.
    catalog_ref: CatalogRef | None = None
    enabled: bool = True


class ModelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2048)
    config: ModelConfig | None = None
    # `pricing` and `catalog_ref` are replaced whole when present; `null` removes them; omitted leaves them.
    pricing: ModelPricingEntry | None = None
    catalog_ref: CatalogRef | None = None
    enabled: bool | None = None


class Model(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    organization_id: str
    workspace_id: str
    provider_id: str
    key: str
    name: str
    description: str
    config: ModelConfig
    pricing: ModelPricingEntry | None
    catalog_ref: CatalogRef | None
    enabled: bool
    version: int
    created_by_id: str
    updated_by_id: str
    created_at: datetime
    updated_at: datetime


class ModelPage(BaseModel):
    items: list[Model]
    next_cursor: str | None


class CatalogModel(BaseModel):
    """A catalog model, with the characteristics and pricing a model created from it starts with."""

    ref: CatalogRef
    # Shared by the copies of one model on every channel, such as `anthropic/claude-opus-5`; `name` is its name.
    identity: str
    name: str
    provider_name: str
    release_date: date
    characteristics: HarnessModelCharacteristics
    pricing: ModelPricingEntry | None
    # Why the catalog's prices are not offered, when it lists prices a `pricing` entry cannot express.
    pricing_warning: str | None


class ModelCatalog(BaseModel):
    """`ready` is fresh, `stale` the last catalog after a failed refresh, `unavailable` none fetched yet."""

    items: list[CatalogModel]
    status: Literal["ready", "stale", "unavailable"]


MEDIA_KINDS: tuple[NativeInputMediaKind, ...] = ("image", "video", "audio")


class MediaUnderstandingSelection(BaseModel):
    """The model, by key, describing each media kind a model cannot read; a kind without one is unavailable."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    image: Key | None = None
    video: Key | None = None
    audio: Key | None = None

    def selections(self) -> dict[NativeInputMediaKind, str]:
        return {kind: key for kind in MEDIA_KINDS if (key := getattr(self, kind)) is not None}


class MediaDefaults(MediaUnderstandingSelection):
    """A workspace's media-understanding models for agents that select none for a kind; `id` is the workspace's."""

    id: str
    version: int
