"""Typed provider recipes and sparse lifecycle choices for internal template tools."""

from pydantic import BaseModel, ConfigDict, Field
from pydantic.experimental.missing_sentinel import MISSING

from a13n_service.infra.ids import ObjectId
from a13n_service.infra.labels import Labels
from a13n_service.providers.registry import TemplateRecipe as Recipe
from a13n_service.resources.environment_templates.schemas import Description, TemplateName


class TemplateCreation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: TemplateName
    provider_id: ObjectId
    description: Description | None = None
    labels: Labels = Field(default_factory=dict)
    recipe: Recipe
    stop_after_seconds: int | None = Field(default=1800, ge=60, le=2592000)
    delete_after_seconds: int | None = Field(default=None, ge=60, le=31536000)


class TemplateEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: TemplateName | MISSING = Field(default=MISSING)
    provider_id: ObjectId | MISSING = Field(default=MISSING)
    description: Description | None = None
    labels: Labels | MISSING = Field(default=MISSING)
    enabled: bool | MISSING = Field(default=MISSING)
    recipe: Recipe | MISSING = Field(default=MISSING)
    stop_after_seconds: int | MISSING | None = Field(default=MISSING, ge=60, le=2592000)
    delete_after_seconds: int | MISSING | None = Field(default=MISSING, ge=60, le=31536000)
