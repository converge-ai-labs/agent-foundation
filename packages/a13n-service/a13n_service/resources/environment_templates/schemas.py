"""Environment templates as the API accepts and returns them; `TemplateConfig` is what lifecycle operations read."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from a13n_service.infra.ids import ObjectId
from a13n_service.infra.labels import Labels

TemplateName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Description = Annotated[str, StringConstraints(max_length=2048)]


class TemplateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # What the provider type builds an instance from (image, resources, network policy, storage), validated by the
    # type's `environment_schema` from `GET /provider-types/environment`.
    recipe: dict[str, JsonValue] = Field(default_factory=dict)
    # The idle policy maintenance reads live: stop a ready environment no run has used for this long, and delete
    # one that no thread mounts either. `null` turns either off.
    stop_after_seconds: int | None = Field(default=1800, ge=60, le=2592000)
    delete_after_seconds: int | None = Field(default=None, ge=60, le=31536000)


class TemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: TemplateName
    description: Description | None = None
    provider_id: ObjectId
    config: TemplateConfig = Field(default_factory=TemplateConfig)
    labels: Labels = Field(default_factory=dict)


class TemplateUpdate(BaseModel):
    """Fields left out stay unchanged; `description: null` clears it.

    A new provider or recipe needs `write` on the provider and applies to environments created afterwards;
    existing ones keep what they were built with, and the idle policy applies to all of them. `enabled: false`
    refuses new environments, including reserved ones never created; created ones keep working.
    """

    model_config = ConfigDict(extra="forbid")
    name: TemplateName | None = None
    description: Description | None = None
    provider_id: ObjectId | None = None
    config: TemplateConfig | None = None
    labels: Labels | None = None
    enabled: bool | None = None


class Template(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    workspace_id: str
    name: str
    description: str | None
    provider_id: str
    config: TemplateConfig
    enabled: bool
    labels: dict[str, str]
    version: int
    created_by_id: str
    updated_by_id: str
    created_at: datetime
    updated_at: datetime


class TemplatePage(BaseModel):
    items: list[Template]
    next_cursor: str | None
